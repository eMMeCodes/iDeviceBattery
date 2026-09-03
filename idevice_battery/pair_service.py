#!/usr/bin/env python3
"""USB detect + lockdown Trust pair + RemotePairing for the Add wizard."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from devices_store import upsert_device

HOME = Path(os.environ.get("HOME", "/data"))
LOCKDOWN_DIR = Path(os.environ.get("IDEVICE_LOCKDOWN", "/var/lib/lockdown"))

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "state": "idle",  # idle|running|need_trust|ok|error
    "phase": "",
    "message": "",
    "device": None,
    "error": None,
    "updated_at": None,
}


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_job() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)
        _job["updated_at"] = _ts()


def list_usb_devices() -> list[dict[str, Any]]:
    """Return connected USB Apple devices (best-effort)."""
    out: list[dict[str, Any]] = []
    try:
        from pymobiledevice3 import usbmux

        for dev in usbmux.list_devices():
            # MuxDevice: serial, connection_type, etc.
            serial = getattr(dev, "serial", None) or getattr(dev, "udid", None)
            if not serial:
                continue
            entry: dict[str, Any] = {
                "udid": str(serial),
                "connection_type": str(getattr(dev, "connection_type", "USB")),
            }
            # Enrich via lockdown if already trusted / connectable
            try:
                from pymobiledevice3.lockdown import create_using_usbmux

                async def _info(udid: str = str(serial)) -> dict[str, Any]:
                    ld = await create_using_usbmux(
                        serial=udid,
                        autopair=False,
                        pairing_records_cache_folder=LOCKDOWN_DIR,
                    )
                    try:
                        return {
                            "name": await ld.get_value(key="DeviceName"),
                            "product_type": await ld.get_value(key="ProductType"),
                        }
                    finally:
                        await ld.close()

                info = asyncio.run(_info())
                entry.update(info)
            except Exception:
                pass
            out.append(entry)
    except Exception as e:
        # Fallback: idevice_id
        try:
            r = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in (r.stdout or "").splitlines():
                udid = line.strip()
                if udid:
                    out.append({"udid": udid, "connection_type": "USB"})
        except Exception:
            raise RuntimeError(f"USB list failed: {e}") from e
    return out


def _pair_record_path(udid: str) -> Path:
    return LOCKDOWN_DIR / f"{udid}.plist"


def _has_lockdown_record(udid: str) -> bool:
    return _pair_record_path(udid).is_file()


async def _lockdown_info(udid: Optional[str], autopair: bool) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_usbmux

    ld = await create_using_usbmux(
        serial=udid,
        autopair=autopair,
        pairing_records_cache_folder=LOCKDOWN_DIR,
    )
    try:
        return {
            "udid": ld.udid,
            "name": await ld.get_value(key="DeviceName"),
            "product_type": await ld.get_value(key="ProductType"),
        }
    finally:
        await ld.close()


async def _pair_lockdown(udid: Optional[str] = None) -> dict[str, Any]:
    """Pair lockdown. Prefer existing Trust; only wait for Trust tap if needed."""
    # 1) Already trusted (device removed from HA but Apple still has this host)
    if udid and _has_lockdown_record(udid):
        _set_job(
            state="running",
            phase="retrust",
            message="Already trusted by this machine — reconnecting (no Trust tap needed)…",
            error=None,
        )
        try:
            return await asyncio.wait_for(_lockdown_info(udid, autopair=False), timeout=25)
        except Exception as e:
            print(f"[pair] existing record failed, will ask Trust: {e}", flush=True)

    # 2) Try without autopair anyway (record may live elsewhere / still valid)
    _set_job(
        state="running",
        phase="lockdown_probe",
        message="Checking if the device already trusts this machine…",
        error=None,
    )
    try:
        return await asyncio.wait_for(_lockdown_info(udid, autopair=False), timeout=12)
    except Exception:
        pass

    # 3) Need Trust on device
    _set_job(
        state="need_trust",
        phase="trust",
        message="If a Trust prompt appears, tap Trust. If none appears, wait — or tap Retry.",
        error=None,
    )
    try:
        return await asyncio.wait_for(_lockdown_info(udid, autopair=True), timeout=90)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            "Timed out waiting for Trust. "
            "If this device was paired before, unlock it and tap Retry — "
            "no Trust prompt may appear because Apple already trusts this machine."
        ) from e


def _remotepair(udid: str) -> None:
    _set_job(
        state="running",
        phase="remotepairing",
        message="Creating RemotePairing record…",
    )
    env = os.environ.copy()
    env["HOME"] = str(HOME)
    # Promptless once lockdown Trust exists
    r = subprocess.run(
        [
            "python3",
            "-m",
            "pymobiledevice3",
            "lockdown",
            "remotepairing",
            "--pair",
            "--udid",
            udid,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "remotepairing failed").strip()
        # Already paired is OK for re-add after Remove
        low = err.lower()
        if "already" in low or "exist" in low:
            print(f"[pair] remotepairing already present: {err}", flush=True)
            try:
                from rsd_battery import backup_remote_pair_records

                backup_remote_pair_records()
            except Exception:
                pass
            return
        raise RuntimeError(err)
    try:
        from rsd_battery import backup_remote_pair_records

        n = backup_remote_pair_records()
        print(f"[pair] remote pairing backed up={n}", flush=True)
    except Exception as e:
        print(f"[pair] remote backup skipped: {e}", flush=True)


def _enable_wifi_connections(udid: str) -> None:
    """Advertise lockdown over Wi‑Fi (mobdev2) — needed for LAN IP discovery."""
    env = os.environ.copy()
    env["HOME"] = str(HOME)
    try:
        r = subprocess.run(
            [
                "python3",
                "-m",
                "pymobiledevice3",
                "lockdown",
                "wifi-connections",
                "on",
                "--udid",
                udid,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if r.returncode != 0:
            print(
                f"[pair] wifi-connections on: {(r.stderr or r.stdout or '').strip()}",
                flush=True,
            )
        else:
            print("[pair] wifi-connections on", flush=True)
    except Exception as e:
        print(f"[pair] wifi-connections failed: {e}", flush=True)


async def _wifi_mac_usb(udid: str) -> str:
    """Wi‑Fi MAC from lockdown over USB (for ARP / mobdev2 matching)."""
    try:
        from pymobiledevice3.lockdown import create_using_usbmux

        ld = await create_using_usbmux(
            serial=udid,
            autopair=False,
            pairing_records_cache_folder=LOCKDOWN_DIR,
        )
        try:
            mac = await ld.get_value(key="WiFiAddress")
            return str(mac or "").strip()
        finally:
            await ld.close()
    except Exception as e:
        print(f"[pair] WiFiAddress: {e}", flush=True)
    return ""


def _ipv4_from_neigh(mac: str) -> str:
    """Map Wi‑Fi MAC → IPv4 via kernel neighbor / ARP tables."""
    import re

    mac_n = (mac or "").lower().replace("-", ":").strip()
    if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac_n):
        return ""
    # ip neigh
    try:
        r = subprocess.run(
            ["ip", "-4", "neigh"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in (r.stdout or "").splitlines():
            low = line.lower()
            if mac_n in low and "FAILED" not in line.upper():
                parts = line.split()
                if parts and _host_rank(parts[0]) == 0:
                    return parts[0]
    except Exception:
        pass
    # /proc/net/arp
    try:
        for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[3].lower() == mac_n and _host_rank(parts[0]) == 0:
                # flags 0x2 = complete
                try:
                    if int(parts[2], 0) & 0x2:
                        return parts[0]
                except ValueError:
                    return parts[0]
    except Exception:
        pass
    return ""


async def _hosts_from_mobdev2(udid: str) -> list[str]:
    """IPs from _apple-mobdev2._tcp Bonjour (lockdown-over-Wi‑Fi), matched by UDID."""
    hosts: list[str] = []
    try:
        from pymobiledevice3.lockdown import get_mobdev2_lockdowns

        async for ip, ld in get_mobdev2_lockdowns(
            udid=udid,
            pair_records=LOCKDOWN_DIR,
            only_paired=True,
        ):
            if ip:
                hosts.append(str(ip))
            try:
                await ld.close()
            except Exception:
                try:
                    await ld.service.close()
                except Exception:
                    pass
    except Exception as e:
        print(f"[pair] mobdev2 browse: {e}", flush=True)
    return hosts


def _host_rank(host: str) -> int:
    """Lower is better: 0=IPv4, 1=IPv6 global/ULA, 2=hostname (.local), 9=skip."""
    import ipaddress
    import re

    h = (host or "").strip()
    if not h:
        return 9
    bare = h.split("%", 1)[0]
    # dotted IPv4
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", bare):
        return 0
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        # mDNS / DNS hostname (e.g. Name.local) — last resort after real IPs
        if ":" not in bare and re.search(r"[A-Za-z]", bare):
            return 2
        return 9
    if isinstance(ip, ipaddress.IPv4Address):
        return 0
    # IPv6
    if ip.is_link_local or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return 9  # fe80::, ::1, etc.
    # global or ULA (fc00::/7 → is_private True in Python)
    if ip.is_global or ip.is_private:
        return 1
    return 9


def _resolve_hostnames(hosts: list[str]) -> list[str]:
    """Expand *.local / DNS names to A/AAAA; keep originals as fallback."""
    import socket

    out: list[str] = list(hosts)
    for h in list(hosts):
        if _host_rank(h) != 2:
            continue
        name = h.strip().rstrip(".")
        # Prefer IPv4 lookups first
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                for _, _, _, _, sockaddr in socket.getaddrinfo(name, None, family):
                    if sockaddr:
                        out.append(str(sockaddr[0]))
            except OSError:
                continue
    return out


async def _bonjour_addresses_for_udid(udid: str) -> list[str]:
    """All Wi‑Fi addresses Bonjour advertises for this UDID (A + AAAA).

    Matches the RemotePairing service to ``udid`` via a short connect attempt,
    then returns every address on that service — not only the one that connected.
    """
    import contextlib

    from pymobiledevice3.bonjour import browse_remotepairing
    from pymobiledevice3.remote.tunnel_service import (
        create_core_device_tunnel_service_using_remotepairing,
    )

    hosts: list[str] = []
    for answer in await browse_remotepairing():
        matched = False
        for address in answer.addresses or []:
            full = getattr(address, "full_ip", None) or getattr(address, "ip", None)
            if not full:
                continue
            try:
                svc = await create_core_device_tunnel_service_using_remotepairing(
                    udid, str(full), answer.port
                )
                matched = True
                with contextlib.suppress(Exception):
                    await svc.close()
                break
            except Exception:
                continue
        if not matched:
            continue
        for address in answer.addresses or []:
            ip = getattr(address, "ip", None)
            if ip:
                hosts.append(str(ip))
            full = getattr(address, "full_ip", None)
            if full:
                hosts.append(str(full))
        # mDNS target (Name.local) — resolve to IPs; do not rely on the name alone
        mdns_host = getattr(answer, "host", None)
        if mdns_host:
            name = str(mdns_host).rstrip(".")
            hosts.append(name)
            hosts.extend(_resolve_hostnames([name]))
        break
    return hosts


def pick_wifi_host(hosts: list[str]) -> str:
    """Prefer IPv4, then global/ULA IPv6, then hostname; never link-local fe80."""
    expanded = _resolve_hostnames(list(hosts))
    ranked = sorted({h for h in expanded if h}, key=_host_rank)
    for h in ranked:
        if _host_rank(h) < 9:
            return h
    return ""


async def discover_wifi_host_async(
    udid: str, attempts: int = 6, delay: float = 2.0
) -> str:
    """Collect real LAN addresses: RemotePairing, mobdev2, ARP — retry while Bonjour wakes."""
    import contextlib

    last_hosts: list[str] = []
    for i in range(max(1, attempts)):
        hosts: list[str] = []
        try:
            hosts.extend(await _bonjour_addresses_for_udid(udid))
        except Exception as e:
            print(f"[pair] remotepairing browse: {e}", flush=True)
        try:
            from pymobiledevice3.remote.tunnel_service import (
                get_remote_pairing_tunnel_services,
            )

            services = await get_remote_pairing_tunnel_services(udid=udid)
            for s in services or []:
                host = getattr(s, "hostname", None)
                if host:
                    hosts.append(str(host))
                with contextlib.suppress(Exception):
                    await s.close()
        except Exception as e:
            print(f"[pair] wifi_host tunnel list: {e}", flush=True)

        hosts.extend(await _hosts_from_mobdev2(udid))

        mac = await _wifi_mac_usb(udid)
        if mac:
            arp_ip = _ipv4_from_neigh(mac)
            print(f"[pair] WiFiAddress={mac} arp={arp_ip!r}", flush=True)
            if arp_ip:
                hosts.append(arp_ip)

        last_hosts = hosts
        chosen = pick_wifi_host(hosts)
        print(
            f"[pair] wifi_host attempt {i + 1}/{attempts}={chosen!r} (from {hosts})",
            flush=True,
        )
        if chosen and _host_rank(chosen) == 0:
            return chosen
        if i + 1 < attempts:
            _set_job(
                state="running",
                phase="wifi_lookup",
                message=f"Looking up Wi‑Fi address… ({i + 1}/{attempts})",
            )
            await asyncio.sleep(delay)
    return pick_wifi_host(last_hosts)


async def wifi_host_from_bonjour_async(udid: str) -> str:
    """Wi‑Fi address discovery ( Bonjour + mobdev2 + ARP )."""
    return await discover_wifi_host_async(udid, attempts=1, delay=0)


def wifi_host_from_bonjour(udid: str) -> str:
    """Sync wrapper for the USB pair job (not under a running event loop)."""
    try:
        return asyncio.run(discover_wifi_host_async(udid))
    except Exception as e:
        print(f"[pair] wifi_host failed: {e}", flush=True)
        traceback.print_exc()
    return ""


def discover_wifi_host(udid: str) -> str:
    """Public sync entry used by pair job and API."""
    return wifi_host_from_bonjour(udid)


def run_pair_job(udid: Optional[str] = None) -> None:
    try:
        _set_job(
            state="running",
            phase="usb",
            message="Looking for USB device…",
            device=None,
            error=None,
        )
        devices = list_usb_devices()
        if udid:
            devices = [d for d in devices if d.get("udid") == udid]
        if not devices:
            _set_job(
                state="error",
                phase="usb",
                message="No device on USB. Check the cable and that the device is unlocked.",
                error="no_usb",
            )
            return

        target = devices[0]
        target_udid = target["udid"]
        if _has_lockdown_record(target_udid):
            _set_job(
                state="running",
                phase="retrust",
                message="Already trusted by this machine — reconnecting (no Trust tap needed)…",
                device=target,
            )
        else:
            _set_job(
                state="running",
                phase="trust_prepare",
                message="Preparing pairing… Unlock the device.",
                device=target,
            )

        info = asyncio.run(_pair_lockdown(target_udid))
        _set_job(
            state="running",
            phase="lockdown_ok",
            message="Lockdown paired.",
            device=info,
        )

        _remotepair(info["udid"])
        _enable_wifi_connections(info["udid"])
        _set_job(
            state="running",
            phase="wifi_lookup",
            message="Looking up Wi‑Fi address…",
            device=info,
        )
        host = discover_wifi_host(info["udid"])
        info["wifi_host"] = host
        # Back-compat for older UI overlays still reading host_guess
        info["host_guess"] = host
        _set_job(
            state="ok",
            phase="ready",
            message=(
                "Pairing complete. Confirm Wi‑Fi address, then continue."
                if host
                else "Pairing complete. Enter the Wi‑Fi IP address, then continue."
            ),
            device=info,
            error=None,
        )
    except Exception as e:
        traceback.print_exc()
        _set_job(
            state="error",
            phase=_job.get("phase") or "error",
            message=str(e),
            error=type(e).__name__,
        )


def start_pair_async(udid: Optional[str] = None, force: bool = False) -> dict[str, Any]:
    with _job_lock:
        # Allow Retry to restart a stuck need_trust / running job
        if not force and _job.get("state") in ("running", "need_trust"):
            return get_job()
        _job.update(
            {
                "state": "running",
                "phase": "starting",
                "message": "Starting…",
                "device": None,
                "error": None,
                "updated_at": _ts(),
            }
        )
    t = threading.Thread(target=run_pair_job, args=(udid,), daemon=True)
    t.start()
    return get_job()


def finish_pair(host: str, name: Optional[str] = None) -> dict[str, Any]:
    job = get_job()
    if job.get("state") != "ok" or not job.get("device"):
        raise RuntimeError("No successful pair to finish")
    host = (host or "").strip()
    if not host:
        raise RuntimeError("Wi‑Fi IP address is required")
    dev = dict(job["device"])
    entry = {
        "udid": dev["udid"],
        "host": host,
        "name": name or dev.get("name") or dev["udid"][:8],
        "product_type": dev.get("product_type") or "",
        "added_at": _ts(),
    }
    store = upsert_device(entry)
    # Immediate poll + MQTT discovery so HA entities exist after Finish
    result = verify_device(entry["udid"], entry["host"])
    mqtt_entry = dict(result)
    mqtt_entry["host"] = entry["host"]
    mqtt_entry["udid"] = entry["udid"]
    try:
        from pathlib import Path
        import json as _json
        from datetime import datetime, timezone
        from model import empty_device_entry, snapshot_root

        out = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
        prev: dict[str, Any] = {}
        try:
            if out.exists():
                prev = _json.loads(out.read_text())
        except Exception:
            prev = {}
        devices_out = []
        for d in store.get("devices") or []:
            if d.get("udid") == entry["udid"]:
                devices_out.append(mqtt_entry)
            else:
                old = next(
                    (e for e in (prev.get("devices") or []) if e.get("udid") == d.get("udid")),
                    None,
                )
                devices_out.append(old or empty_device_entry(d))
        doc = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": "remotepairing-userspace-rsd",
            "devices": devices_out,
            "error": mqtt_entry.get("error"),
        }
        doc.update(snapshot_root(devices_out, prev))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(_json.dumps(doc, indent=2, default=str))
        tmp.replace(out)
    except Exception as e:
        print(f"[pair] battery json update failed: {e}", flush=True)
    try:
        from mqtt_ha import resolve_entities_for_entry, sync_entry

        sync_entry(mqtt_entry)
        entities = resolve_entities_for_entry(mqtt_entry)
    except Exception as e:
        print(f"[mqtt] finish_pair sync failed: {e}", flush=True)
        entities = []
    _set_job(state="idle", phase="", message="Device saved.", device=entry, error=None)
    return {
        "device": entry,
        "store": store,
        "verify": result,
        "entities": entities,
    }


def verify_device(udid: str, host: str) -> dict[str, Any]:
    """One-shot poll for wizard Verify step."""
    import rsd_battery as rb

    rb.UDID = udid
    rb.HOST = host
    dev = {"udid": udid, "host": host}
    return asyncio.run(rb.fetch_device(dev, {}))
