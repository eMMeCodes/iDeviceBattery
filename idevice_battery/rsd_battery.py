#!/usr/bin/env python3
"""Device + accessory battery over Wi-Fi (AirBattery-equivalent values on Linux).

Device (iPhone / iPad): lockdown TCP :62078 + pair record → com.apple.mobile.battery
Accessory (Watch, AirPods, Pencil, …): RemotePairing → RSD → CompanionProxy

Reads paired devices from /data/devices.json (see devices_store.py).
Identity is UDID. Accessories are optional and whatever CompanionProxy lists.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from devices_store import (
    load_store,
    patch_device,
    read_battery_doc,
    registered_udids,
    write_battery_doc,
)
from model import (
    accessories_from_entry,
    classify_kind,
    device_battery,
    mark_accessories_stale,
    merge_accessories,
    normalize_accessory,
)

LOCKDOWN_DIR = Path(os.environ.get("IDEVICE_LOCKDOWN", "/var/lib/lockdown"))
DEFAULT_MTU = int(os.environ.get("IDEVICE_CDTUNNEL_MTU", "16000"))
LOCKDOWN_TIMEOUT = float(os.environ.get("IDEVICE_LOCKDOWN_TIMEOUT", "20"))
ACCESSORY_TIMEOUT = float(os.environ.get("IDEVICE_ACCESSORY_TIMEOUT", "25"))
FETCH_ONCE_TIMEOUT = float(os.environ.get("IDEVICE_FETCH_TIMEOUT", "55"))
HOST_REFRESH_TIMEOUT = float(os.environ.get("IDEVICE_HOST_REFRESH_TIMEOUT", "12"))
CONNECT_TIMEOUT = float(os.environ.get("IDEVICE_CONNECT_TIMEOUT", "4"))

# One Bonjour browse and one accessory teardown at a time. Cancelling either
# leaves the SSL fd open (Python 3.10 wait_for); the next poll died on EMFILE.
_browse_task: asyncio.Task | None = None
_accessory_inflight: asyncio.Task | None = None
_accessory_cleanup: asyncio.Task | None = None


def _ensure_pem(data: bytes, kind: str) -> bytes:
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    if b"BEGIN" in data:
        return data if data.endswith(b"\n") else data + b"\n"
    import base64

    return (
        f"-----BEGIN {kind}-----\n"
        f"{base64.encodebytes(data).decode()}"
        f"-----END {kind}-----\n"
    ).encode()


def load_pair_record(udid: str) -> dict[str, Any]:
    plist_path = LOCKDOWN_DIR / f"{udid}.plist"
    if not plist_path.exists():
        raise FileNotFoundError(f"missing pair record {plist_path}")
    rec = dict(__import__("plistlib").loads(plist_path.read_bytes()))
    rec["HostCertificate"] = _ensure_pem(rec["HostCertificate"], "CERTIFICATE")
    rec["HostPrivateKey"] = _ensure_pem(rec["HostPrivateKey"], "PRIVATE KEY")
    return rec


def _load_prev() -> dict[str, Any]:
    return read_battery_doc()


def _stale_doc(prev: dict[str, Any], error: str) -> dict[str, Any]:
    devices = []
    for entry in prev.get("devices") or []:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        if row.get("battery_level") is not None:
            row["stale"] = True
        row["error"] = error
        acc = accessories_from_entry(row)
        row["accessories"] = mark_accessories_stale(acc)
        devices.append(row)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": "remotepairing-userspace-rsd",
        "devices": devices,
        "error": error,
    }


async def _refresh_device_host(dev: dict[str, Any]) -> bool:
    """Keep stored IPv4 if :62078 is open; otherwise Bonjour/mobdev2 without tunnels."""
    from pair_service import discover_wifi_host_async, probe_lockdown_port

    host = str(dev.get("host") or "")
    udid = str(dev.get("udid") or "")
    if not udid:
        return False
    try:
        if host and await probe_lockdown_port(host):
            return False
        better = await asyncio.wait_for(
            discover_wifi_host_async(
                udid,
                attempts=1,
                delay=0,
                skip_tunnel=True,
                prefer_host=host,
            ),
            timeout=HOST_REFRESH_TIMEOUT,
        )
    except Exception as e:
        print(f"[poll] host refresh {udid[:8]}: {e}", flush=True)
        return False
    if not better or better == host:
        return False
    print(f"[poll] host {host!r} → {better!r} for {udid[:8]}", flush=True)
    if not patch_device(udid, host=better):
        return False
    dev["host"] = better
    return True


async def _device_battery_live(
    rec: dict[str, Any], host: str, udid: str, dev: dict[str, Any]
) -> dict[str, Any]:
    """Lockdown device read with host rediscovery + one retry on timeout."""
    last_err: Exception | None = None
    use_host = host
    for attempt in range(2):
        try:
            return await _device_battery(rec, host=use_host, udid=udid)
        except Exception as e:
            last_err = e
            if attempt == 0 and isinstance(e, (asyncio.TimeoutError, TimeoutError, OSError)):
                if await _refresh_device_host(dev):
                    use_host = str(dev["host"])
                    print(f"[poll] retry lockdown {udid[:8]} @ {use_host}", flush=True)
                    continue
            break
    assert last_err is not None
    raise last_err


def _normalize_device_battery(
    batt: dict[str, Any],
    *,
    udid: str,
    name: Any = None,
    product: Any = None,
    source: str = "lockdown",
) -> dict[str, Any]:
    pct = batt.get("BatteryCurrentCapacity")
    full = bool(batt.get("FullyCharged"))
    charging = bool(batt.get("BatteryIsCharging"))
    plugged = bool(batt.get("ExternalConnected"))
    if charging or plugged:
        state = "full" if full else "charging"
    else:
        state = "Not Charging"
    return {
        "role": "device",
        "kind": classify_kind(product, udid),
        "battery_level": int(pct) if pct is not None else None,
        "battery_state": state,
        "name": name,
        "product_type": product,
        "raw": batt,
        "source": source,
    }


def pick_remotepairing_service(services: list[Any], prefer_host: str = "") -> Any | None:
    """One RemotePairing endpoint: prefer stored IPv4, then best-ranked hostname.

    Bonjour often returns the same phone dozens of times (n=36). Opening more
    than one tunnel is waste, not extra coverage.
    """
    from pair_service import _host_rank

    if not services:
        return None
    seen: set[tuple[str, Any]] = set()
    unique: list[Any] = []
    for s in services:
        host = str(getattr(s, "hostname", "") or "")
        port = getattr(s, "port", None)
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    prefer = (prefer_host or "").strip()
    if prefer:
        for s in unique:
            if str(getattr(s, "hostname", "") or "") == prefer:
                return s
    unique.sort(
        key=lambda s: _host_rank(str(getattr(s, "hostname", "") or ""))
    )
    return unique[0]


async def _close_one(obj: Any) -> None:
    """Close one Apple client. Caller must not cancel this coroutine."""
    if obj is None:
        return
    writer = None
    if isinstance(obj, tuple) and len(obj) == 2 and hasattr(obj[1], "close"):
        writer = obj[1]
    if writer is not None:
        try:
            writer.close()
            wait = getattr(writer, "wait_closed", None)
            if wait:
                await wait()
        except Exception:
            pass
        return
    close = getattr(obj, "close", None)
    if close is None:
        service = getattr(obj, "service", None)
        if service is not None and service is not obj:
            await _close_one(service)
        return
    try:
        res = close()
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass


def close_later(obj: Any) -> None:
    """Schedule a close that survives cancellation of the current task."""
    if obj is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.create_task(_close_one(obj))


async def _close_task_result(task: asyncio.Task) -> None:
    try:
        if task.cancelled():
            return
        result = task.result()
    except Exception:
        return
    await _close_one(result)


async def settle(
    task: asyncio.Task,
    timeout: float,
    *,
    on_abandon: Any = None,
) -> Any:
    """Wait without cancelling ``task``.

    ``asyncio.wait_for`` cancels the handshake. SSL ``wait_closed`` then never
    runs and the fd stays open until the process hits EMFILE. On timeout or
    cancellation the task keeps running; ``on_abandon`` (or a close of the
    result) runs when it finishes.
    """

    def abandon(done: asyncio.Task) -> None:
        if on_abandon is not None:
            try:
                on_abandon()
            except Exception:
                pass
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(_close_task_result(done))

    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        if task.done():
            abandon(task)
        else:
            task.add_done_callback(abandon)
        raise
    if task not in done:
        task.add_done_callback(abandon)
        return None
    if task.cancelled():
        return None
    exc = task.exception()
    if exc is not None:
        raise exc
    return task.result()


async def wait_owned(coro: Any, timeout: float, *, on_abandon: Any = None) -> Any:
    return await settle(asyncio.create_task(coro), timeout, on_abandon=on_abandon)


async def _close_pairing_services(services: list[Any], keep: Any | None = None) -> None:
    """Close each socket on its own, without a shared timeout that cancels them all."""
    seen: set[int] = set()
    for service in services:
        if keep is not None and service is keep:
            continue
        ident = id(service)
        if ident in seen:
            continue
        seen.add(ident)
        await _close_one(service)


def remotepairing_endpoints(
    answers: list[Any], prefer_host: str = ""
) -> list[tuple[str, int]]:
    """Unique Bonjour endpoints. Saved IPv4 first, then other IPv4. No socket."""
    from pair_service import _host_rank

    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for answer in answers or []:
        port = getattr(answer, "port", None)
        if not isinstance(port, int):
            continue
        ips: list[str] = []
        for address in getattr(answer, "addresses", None) or []:
            ip = getattr(address, "full_ip", None) or getattr(address, "ip", None)
            if ip:
                ips.append(str(ip))
        host = str(getattr(answer, "host", "") or "").rstrip(".")
        if host:
            ips.append(host)
        for ip in ips:
            if _host_rank(ip) >= 9:
                continue
            key = (ip, port)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    prefer = (prefer_host or "").strip()
    out.sort(
        key=lambda item: (
            0 if prefer and item[0] == prefer else 1,
            _host_rank(item[0]),
            item[0],
        )
    )
    return out


async def _device_battery_from_rsd(rsd: Any, udid: str) -> dict[str, Any] | None:
    """iPhone/iPad % over the same RemotePairing tunnel used for accessories."""
    batt: Any = None
    try:
        batt = await rsd.get_value(domain="com.apple.mobile.battery")
    except TypeError:
        try:
            batt = await rsd.get_value("com.apple.mobile.battery")
        except Exception as e:
            print(f"[poll] rsd battery: {e}", flush=True)
            batt = None
    except Exception as e:
        print(f"[poll] rsd battery: {e}", flush=True)
        batt = None
    if not isinstance(batt, dict) or batt.get("BatteryCurrentCapacity") is None:
        return None
    name = product = None
    try:
        name = await rsd.get_value(key="DeviceName")
    except Exception:
        pass
    try:
        product = await rsd.get_value(key="ProductType")
    except Exception:
        pass
    if not product:
        try:
            props = (getattr(rsd, "peer_info", None) or {}).get("Properties") or {}
            product = props.get("ProductType")
            name = name or props.get("DeviceName")
        except Exception:
            pass
    live = _normalize_device_battery(
        batt, udid=udid, name=name, product=product, source="remotepairing-rsd"
    )
    if live.get("battery_level") is None:
        return None
    return live


async def _device_battery(rec: dict[str, Any], host: str, udid: str) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_tcp

    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCKDOWN_TIMEOUT

    def remaining() -> float:
        return max(0.1, deadline - loop.time())

    ld = await wait_owned(
        create_using_tcp(
            hostname=host,
            identifier=udid,
            autopair=False,
            pair_record=rec,
            pairing_records_cache_folder=LOCKDOWN_DIR,
            keep_alive=False,
        ),
        remaining(),
    )
    if ld is None:
        raise TimeoutError(f"lockdown connect {host}")

    async def _read() -> tuple[Any, Any, Any]:
        batt = await ld.get_value(domain="com.apple.mobile.battery")
        name = await ld.get_value(key="DeviceName")
        product = await ld.get_value(key="ProductType")
        return batt, name, product

    # Own the client only after the read returns. A timeout leaves the read
    # running; its done-callback closes the client when the read finishes.
    release = True
    try:
        got = await wait_owned(_read(), remaining(), on_abandon=lambda: close_later(ld))
        if got is None:
            release = False
            raise TimeoutError(f"lockdown read {host}")
        batt, name, product = got
        return _normalize_device_battery(
            batt if isinstance(batt, dict) else {},
            udid=udid,
            name=name,
            product=product,
            source="lockdown",
        )
    except asyncio.CancelledError:
        release = False
        raise
    finally:
        if release:
            close_later(ld)


async def _fetch_companion_device(
    companion: Any, device_udid: str
) -> dict[str, Any] | None:
    name = product = level = is_charging = None
    for key in (
        "DeviceName",
        "ProductType",
        "BatteryCurrentCapacity",
        "BatteryIsCharging",
    ):
        try:
            val = await companion.get_value(device_udid, key)
        except Exception as e:
            print(f"COMPANION_{device_udid[:8]}_{key}_FAIL {e}", flush=True)
            continue
        if isinstance(val, dict) and key in val:
            val = val[key]
        if key == "DeviceName":
            name = val
        elif key == "ProductType":
            product = val
        elif key == "BatteryCurrentCapacity":
            level = int(val) if val is not None else None
        elif key == "BatteryIsCharging":
            is_charging = bool(val)

    if level is None and not name and not product:
        return None

    plugged = bool(is_charging)
    state = "charging" if plugged else "Not Charging"
    return normalize_accessory(
        {
            "udid": device_udid,
            "name": name,
            "product_type": product,
            "battery_level": level,
            "battery_state": state,
        }
    )


def _companion_item_udid(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("UDID") or item.get("udid") or item)
    return str(item)


HOME_DIR = Path(os.environ.get("HOME", "/data"))
REMOTE_DIR = HOME_DIR / ".pymobiledevice3"
REMOTE_BACKUP = Path("/share/idevice_remotepairing_backup")


def remote_pair_path(udid: str) -> Path:
    return REMOTE_DIR / f"remote_{udid}.plist"


def has_remote_pair_record(udid: str) -> bool:
    return remote_pair_path(udid).is_file()


def backup_remote_pair_records() -> int:
    """Copy remote_*.plist to share so reinstalls keep the accessory path."""
    REMOTE_BACKUP.mkdir(parents=True, exist_ok=True)
    n = 0
    if not REMOTE_DIR.is_dir():
        return 0
    for p in REMOTE_DIR.glob("remote_*.plist"):
        try:
            dest = REMOTE_BACKUP / p.name
            dest.write_bytes(p.read_bytes())
            n += 1
        except Exception as e:
            print(f"[migrate] remote backup {p.name}: {e}", flush=True)
    return n


def restore_remote_pair_records() -> int:
    """Restore remote_*.plist from share if missing in /data."""
    if not REMOTE_BACKUP.is_dir():
        return 0
    REMOTE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in REMOTE_BACKUP.glob("remote_*.plist"):
        dest = REMOTE_DIR / p.name
        if dest.exists():
            continue
        try:
            dest.write_bytes(p.read_bytes())
            n += 1
        except Exception as e:
            print(f"[migrate] remote restore {p.name}: {e}", flush=True)
    return n


async def diagnose_companion_async(udid: str, host: str | None = None) -> dict[str, Any]:
    """Remote plist + Bonjour browse status (safe inside the poll event loop)."""
    diag: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "udid": udid,
        "host": host,
        "home": str(HOME_DIR),
        "remote_plist": str(remote_pair_path(udid)),
        "remote_plist_exists": has_remote_pair_record(udid),
        "remote_files": [],
        "bonjour_services": 0,
        "bonjour_hosts": [],
        "error": None,
    }
    try:
        if REMOTE_DIR.is_dir():
            diag["remote_files"] = sorted(p.name for p in REMOTE_DIR.glob("remote_*.plist"))
    except Exception as e:
        diag["remote_list_error"] = str(e)

    try:
        answers = await _browse_answers()
        diag["bonjour_services"] = len(answers or [])
        hosts = []
        for a in answers or []:
            addrs = []
            for address in getattr(a, "addresses", None) or []:
                ip = getattr(address, "ip", None) or getattr(address, "full_ip", None)
                if ip:
                    addrs.append(str(ip))
            hosts.append(
                {
                    "port": getattr(a, "port", None),
                    "host": str(getattr(a, "host", "") or ""),
                    "addresses": addrs[:6],
                }
            )
        diag["bonjour_hosts"] = hosts
    except Exception as e:
        diag["error"] = f"{type(e).__name__}: {e}"

    print(
        f"[diag] remote_plist={diag['remote_plist_exists']} "
        f"files={diag['remote_files']} bonjour={diag['bonjour_services']}",
        flush=True,
    )
    return diag


async def _browse_answers() -> list[Any]:
    """One Bonjour browse. A browse already in flight is left alone."""
    global _browse_task
    if _browse_task is not None and not _browse_task.done():
        print("REMOTEPAIRING browse still in flight", flush=True)
        return []
    from pymobiledevice3.bonjour import browse_remotepairing

    _browse_task = asyncio.create_task(browse_remotepairing(timeout=4.0))
    try:
        answers = await settle(_browse_task, 5)
    except Exception as e:
        print(f"REMOTEPAIRING browse: {e}", flush=True)
        return []
    return list(answers or [])


async def _browse_remotepairing_services(
    udid: str,
    prefer_host: str = "",
    attempts: int = 2,
    delay: float = 1.2,
):
    """One live RemotePairing socket, saved Wi‑Fi address first.

    ``get_remote_pairing_tunnel_services`` connects every Bonjour address and
    returns them all (this phone has been n=40). Those fds stayed open after
    the 3s close and the poller hit ``Too many open files`` on the lock files.
    A second browse runs only when the first saw no endpoints.
    """
    from pymobiledevice3.remote.tunnel_service import (
        create_core_device_tunnel_service_using_remotepairing,
    )

    if not has_remote_pair_record(udid):
        print(
            f"REMOTE_PAIR_MISSING {udid[:8]}… "
            f"expected {remote_pair_path(udid)} — USB: + Add wizard (RemotePairing)",
            flush=True,
        )
        return []

    for i in range(max(1, attempts)):
        answers = await _browse_answers()
        endpoints = remotepairing_endpoints(answers, prefer_host)
        if not endpoints:
            print(
                f"REMOTEPAIRING browse {i + 1}/{attempts}: no endpoints",
                flush=True,
            )
            # A browse that has not finished yet will not grow new answers
            # on a second call; leave that task alone.
            if _browse_task is not None and not _browse_task.done():
                return []
            if i + 1 < attempts:
                await asyncio.sleep(delay)
            continue
        for host, port in endpoints[:2]:
            try:
                conn = await wait_owned(
                    create_core_device_tunnel_service_using_remotepairing(udid, host, port),
                    CONNECT_TIMEOUT,
                )
            except Exception as e:
                print(
                    f"REMOTEPAIRING connect {host}:{port}: {type(e).__name__}",
                    flush=True,
                )
                continue
            if conn is None:
                print(f"REMOTEPAIRING connect {host}:{port}: timeout", flush=True)
                continue
            print(
                f"REMOTEPAIRING picked {host}:{port} candidates={len(endpoints)}",
                flush=True,
            )
            return [conn]
        return []
    return []


async def _cleanup_accessory_session(services: list[Any], stack: AsyncExitStack) -> None:
    try:
        await _close_pairing_services(services)
        await stack.aclose()
    except Exception:
        pass
    finally:
        try:
            from pymobiledevice3.remote import tunnel_service

            tunnel_service.USE_USERSPACE_TUNNEL = False
        except Exception:
            pass


async def _accessories_via_remotepairing(
    host: str | None = None, udid: str | None = None
) -> dict[str, Any]:
    """List accessories exposed by a paired device via CompanionProxy."""
    global _accessory_cleanup, _accessory_inflight
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane, UserspaceTun
    from pymobiledevice3.services.companion import CompanionProxyService

    use_udid = str(udid or "")
    use_host = str(host or "")
    result: dict[str, Any] = {"accessories": [], "device": None, "error": None}
    if not use_udid:
        result["error"] = "udid required"
        return result

    # A timed-out poll leaves the previous open running so its close still
    # happens. Do not stack another tunnel behind it.
    current = asyncio.current_task()
    previous = _accessory_inflight
    if previous is not None and previous is not current and not previous.done():
        result["error"] = "previous RemotePairing still open"
        return result
    _accessory_inflight = current

    if _accessory_cleanup is not None and not _accessory_cleanup.done():
        try:
            await asyncio.shield(_accessory_cleanup)
        except Exception:
            pass

    tunnel_service.USE_USERSPACE_TUNNEL = True
    tunnel_service.RemotePairingTcpTunnel.REQUESTED_MTU = DEFAULT_MTU
    stack = AsyncExitStack()
    services: list[Any] = []
    try:
        if not has_remote_pair_record(use_udid):
            print(
                f"ACCESSORY_SKIP {use_udid[:8]}… no RemotePairing record (device battery still works)",
                flush=True,
            )
            return result
        services = await _browse_remotepairing_services(use_udid, prefer_host=use_host)
        if not services:
            raise RuntimeError(
                "no RemotePairing on Bonjour; unlock the device and keep Wi‑Fi on"
            )
        provider = pick_remotepairing_service(services, use_host)
        if provider is None:
            raise RuntimeError("no RemotePairing service after dedupe")
        await _close_pairing_services(services, keep=provider)
        print(
            f"REMOTEPAIRING host={getattr(provider, 'hostname', '?')} "
            f"port={getattr(provider, 'port', '?')} n={len(services)} picked=1",
            flush=True,
        )
        stack.push_async_callback(provider.close)
        services = []
        tunnel_result = await stack.enter_async_context(provider.start_tcp_tunnel())
        print(f"TUNNEL_OK {tunnel_result.address} rsd={tunnel_result.port}", flush=True)
        tun = cast(UserspaceTun, tunnel_result.client.tun)
        tun.set_peer(tunnel_result.address)
        dial = await stack.enter_async_context(
            UserspaceDialPlane(tun, tunnel_result.address)
        )
        rsd = RemoteServiceDiscoveryService(
            (tunnel_result.address, tunnel_result.port),
            open_connection=dial.dial,
            auxiliary_metadata=tunnel_result.auxiliary_metadata,
        )
        stack.push_async_callback(rsd.close)
        await rsd.connect()
        print("RSD_OK", flush=True)

        rsd_device = await _device_battery_from_rsd(rsd, use_udid)
        if rsd_device:
            result["device"] = rsd_device
            print(
                f"RSD_DEVICE {use_udid[:8]}… "
                f"{rsd_device.get('battery_level')}% {rsd_device.get('battery_state')}",
                flush=True,
            )

        companion = CompanionProxyService(rsd)
        listed = await companion.list()
        print(f"COMPANION_LIST {listed}", flush=True)
        if not listed:
            return result

        accessories: list[dict[str, Any]] = []
        for item in listed:
            dev_udid = _companion_item_udid(item)
            info = await _fetch_companion_device(companion, dev_udid)
            if not info:
                continue
            if info.get("udid") == use_udid:
                if not result.get("device") and info.get("battery_level") is not None:
                    result["device"] = {
                        **info,
                        "role": "device",
                        "kind": classify_kind(info.get("product_type"), use_udid),
                        "source": "companion",
                    }
                    print(
                        f"DEVICE_OK companion {use_udid[:8]}… "
                        f"{info['battery_level']}% {info.get('battery_state')}",
                        flush=True,
                    )
                continue
            if info.get("battery_level") is None:
                print(f"ACCESSORY_SKIP {dev_udid[:8]} no battery", flush=True)
                continue
            accessories.append(info)
            print(
                f"ACCESSORY_OK {info.get('kind')} {info.get('name')} "
                f"{info['battery_level']}% {info.get('battery_state')}",
                flush=True,
            )

        if accessories:
            try:
                backup_remote_pair_records()
            except Exception:
                pass

        result["accessories"] = accessories
        return result
    finally:
        # Detach the teardown. Awaiting it here is what a poll timeout cancels,
        # and a cancelled SSL close leaves the fd open. The next open waits.
        pending = list(services)
        _accessory_cleanup = asyncio.create_task(
            _cleanup_accessory_session(pending, stack)
        )


async def fetch_device(dev: dict[str, Any], prev_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Poll one USB-paired device (UDID) then optionally its companion accessories."""
    udid = str(dev["udid"])
    host = str(dev.get("host") or "")
    prev_entry = prev_entry or {}
    prev = device_battery(prev_entry)
    prev_level = prev.get("battery_level")
    prev_state = prev.get("battery_state")
    prev_stale = bool(prev.get("stale"))
    prev_acc = accessories_from_entry(prev_entry)
    name = dev.get("name") or prev.get("name")
    product = dev.get("product_type") or prev.get("product_type")
    entry: dict[str, Any] = {
        "udid": udid,
        "host": host,
        "name": name,
        "product_type": product,
        "role": "device",
        "kind": classify_kind(product, udid),
        "battery_level": prev_level,
        "battery_state": prev_state,
        "raw": prev.get("raw"),
        "stale": prev_stale,
        "updated_at": prev.get("updated_at"),
        "accessories": prev_acc,
        "error": None,
        "accessories_error": None,
    }
    errors: list[str] = []
    device_ok = False
    try:
        rec = load_pair_record(udid)
        live = await _device_battery_live(rec, host=host, udid=udid, dev=dev)
        device_ok = True
        entry["host"] = str(dev.get("host") or host)
        entry["battery_level"] = live.get("battery_level")
        entry["battery_state"] = live.get("battery_state")
        entry["raw"] = live.get("raw")
        entry["name"] = live.get("name") or entry["name"]
        entry["product_type"] = live.get("product_type") or entry["product_type"]
        entry["kind"] = classify_kind(entry["product_type"], udid)
        entry["stale"] = False
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            fields: dict[str, Any] = {}
            if entry["name"]:
                fields["name"] = entry["name"]
            if entry["product_type"]:
                fields["product_type"] = entry["product_type"]
            if entry.get("host"):
                fields["host"] = entry["host"]
            if fields:
                patch_device(udid, **fields)
        except Exception:
            pass
        print(
            f"DEVICE_OK {entry['kind']} {udid[:8]}… "
            f"{entry['battery_level']}% {entry['battery_state']}",
            flush=True,
        )
    except Exception as e:
        errors.append(f"device: {type(e).__name__}: {e}")
        print(f"DEVICE_FAIL {type(e).__name__}: {e}", flush=True)
        if prev_level is not None:
            entry["stale"] = True
        else:
            entry["battery_level"] = None
            entry["stale"] = False

    accessories_ok = False
    merged = False
    try:
        scan_task = asyncio.create_task(
            _accessories_via_remotepairing(host=entry.get("host") or host, udid=udid)
        )
        done, _pending = await asyncio.wait({scan_task}, timeout=ACCESSORY_TIMEOUT)
        if scan_task not in done:
            # Leave the task running so its teardown still closes the socket.
            raise TimeoutError(
                f"accessories timed out after {int(ACCESSORY_TIMEOUT)}s"
            )
        scan = scan_task.result()
        found = scan.get("accessories") or []
        ts = datetime.now(timezone.utc).isoformat()
        entry["accessories"] = merge_accessories(prev_acc, found, ts)
        merged = True
        accessories_ok = bool(found)
        if not device_ok:
            rsd_live = scan.get("device")
            if (
                isinstance(rsd_live, dict)
                and rsd_live.get("battery_level") is not None
            ):
                device_ok = True
                entry["battery_level"] = rsd_live.get("battery_level")
                entry["battery_state"] = rsd_live.get("battery_state")
                entry["raw"] = rsd_live.get("raw")
                entry["name"] = rsd_live.get("name") or entry["name"]
                entry["product_type"] = rsd_live.get("product_type") or entry["product_type"]
                entry["kind"] = classify_kind(entry["product_type"], udid)
                entry["stale"] = False
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                entry["error"] = None
                errors = [e for e in errors if not e.startswith("device:")]
                src = rsd_live.get("source") or "remotepairing-rsd"
                try:
                    fields: dict[str, Any] = {}
                    if entry["name"]:
                        fields["name"] = entry["name"]
                    if entry["product_type"]:
                        fields["product_type"] = entry["product_type"]
                    if fields:
                        patch_device(udid, **fields)
                except Exception:
                    pass
                print(
                    f"DEVICE_OK {src} {entry['kind']} {udid[:8]}… "
                    f"{entry['battery_level']}% {entry['battery_state']}",
                    flush=True,
                )
        if scan.get("error") and not found:
            errors.append(f"accessories: {scan['error']}")
            print(f"ACCESSORY_FAIL {scan['error']}", flush=True)
        elif scan.get("error"):
            print(f"ACCESSORY_NOTE {scan['error']}", flush=True)
    except Exception as e:
        errors.append(f"accessories: {type(e).__name__}: {e}")
        print(f"ACCESSORY_FAIL {type(e).__name__}: {e}", flush=True)

    if not merged:
        entry["accessories"] = mark_accessories_stale(list(prev_acc))

    if errors:
        if not device_ok:
            entry["error"] = "; ".join(errors)
        else:
            entry["accessories_error"] = "; ".join(
                e for e in errors if e.startswith("accessories:")
            ) or None
            entry["error"] = None
    return entry


async def fetch_once() -> dict[str, Any]:
    store = load_store()
    devices = list(store.get("devices") or [])
    # One phone at a time. A parallel gather shares one timeout, so the
    # cancellation of a hung tunnel also drops the other phone's sockets.
    for dev in devices:
        await _refresh_device_host(dev)

    prev = _load_prev()
    prev_by = {d.get("udid"): d for d in (prev.get("devices") or []) if d.get("udid")}

    doc: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": "remotepairing-userspace-rsd",
        "devices": [],
        "error": None,
    }

    if not devices:
        doc["error"] = "no paired devices — open the add-on UI and tap Add"
        return doc

    errors: list[str] = []
    results = []
    for dev in devices:
        try:
            results.append(await fetch_device(dev, prev_by.get(dev["udid"])))
        except Exception as exc:
            results.append(exc)
    still = registered_udids()
    for dev, result in zip(devices, results):
        if dev.get("udid") not in still:
            continue
        if isinstance(result, BaseException):
            prev_entry = prev_by.get(dev.get("udid")) or {}
            entry = dict(prev_entry) if prev_entry else {
                "udid": dev.get("udid"),
                "host": dev.get("host"),
                "name": dev.get("name"),
                "stale": True,
            }
            entry["error"] = f"{type(result).__name__}: {result}"
            entry["stale"] = True
            print(f"DEVICE_FAIL {type(result).__name__}: {result}", flush=True)
            doc["devices"].append(entry)
            errors.append(f"{str(dev.get('udid') or '')[:8]}: {entry['error']}")
            continue
        doc["devices"].append(result)
        if result.get("error"):
            errors.append(f"{dev['udid'][:8]}: {result['error']}")

    if not doc["devices"] and not still:
        doc["error"] = "no paired devices — open the add-on UI and tap Add"
        return doc

    if errors:
        doc["error"] = "; ".join(errors)
    return doc


def _write(doc: dict[str, Any]) -> None:
    still = registered_udids()
    doc["devices"] = [e for e in (doc.get("devices") or []) if e.get("udid") in still]
    if not still:
        doc["devices"] = []
        doc["error"] = "no paired devices — open the add-on UI and tap Add"
    write_battery_doc(doc)
    try:
        from mqtt_ha import sync_battery_doc

        sync_battery_doc(doc)
    except Exception as e:
        print(f"[mqtt] poll sync failed: {e}", flush=True)


async def poll_cycle() -> dict[str, Any]:
    """One poll: timeout the whole fetch, always return a JSON-ready doc."""
    try:
        return await asyncio.wait_for(fetch_once(), timeout=FETCH_ONCE_TIMEOUT)
    except asyncio.TimeoutError:
        err = f"poll timed out after {int(FETCH_ONCE_TIMEOUT)}s"
        print("FETCH_FAIL", err, flush=True)
        return _stale_doc(_load_prev(), err)
    except Exception as e:
        doc = _stale_doc(_load_prev(), f"{type(e).__name__}: {e}")
        print("FETCH_FAIL", doc["error"], flush=True)
        traceback.print_exc()
        return doc


def _open_fd_count() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


async def loop() -> int:
    while True:
        store = load_store()
        poll = int(store.get("poll_seconds") or os.environ.get("IDEVICE_POLL_SEC") or 180)
        before = _open_fd_count()
        doc = await poll_cycle()
        _write(doc)
        after = _open_fd_count()
        if before >= 0 and after >= 0:
            print(f"[fd] open files {before} → {after}", flush=True)
        # 1024 is the container default. Dying inside the lock open left HA
        # without a publish for the rest of the day.
        if after > 512:
            print(f"[fd] {after} open files — exiting so the app restarts clean", flush=True)
            return 75
        await asyncio.sleep(poll)


def main() -> int:
    store = load_store()
    if "--once" in sys.argv:
        if not (store.get("devices") or []):
            print("No paired devices", flush=True)
            return 1
        doc = asyncio.run(poll_cycle())
        print(json.dumps(doc, indent=2, default=str))
        _write(doc)
        return 0 if doc.get("devices") else 1
    return asyncio.run(loop())


if __name__ == "__main__":
    raise SystemExit(main())
