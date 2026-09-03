#!/usr/bin/env python3
"""Device + accessory battery over Wi-Fi (AirBattery-equivalent values on Linux).

Device (iPhone / iPad): lockdown TCP :62078 + pair record → com.apple.mobile.battery
Accessory (Watch, AirPods, …): RemotePairing → userspace CDTunnel → RSD → CompanionProxy

Reads paired devices from /data/devices.json (see devices_store.py).
Keeps top-level phone/watch fields for the primary device (legacy HA sensors).
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

from devices_store import load_store, primary_device, save_store
from model import (
    accessories_from_entry,
    apply_legacy_aliases,
    classify_kind,
    mark_accessories_stale,
    normalize_accessory,
    snapshot_root,
)

OUT = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
LOCKDOWN_DIR = Path(os.environ.get("IDEVICE_LOCKDOWN", "/var/lib/lockdown"))
DEFAULT_MTU = int(os.environ.get("IDEVICE_CDTUNNEL_MTU", "16000"))
LOCKDOWN_TIMEOUT = float(os.environ.get("IDEVICE_LOCKDOWN_TIMEOUT", "20"))

# Mutable for verify/wizard one-shots
UDID = os.environ.get("IDEVICE_UDID", "").strip()
HOST = os.environ.get("IDEVICE_HOST", "").strip()


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


def load_pair_record(udid: str | None = None) -> dict[str, Any]:
    uid = udid or UDID
    plist_path = LOCKDOWN_DIR / f"{uid}.plist"
    if not plist_path.exists():
        raise FileNotFoundError(f"missing pair record {plist_path}")
    rec = dict(__import__("plistlib").loads(plist_path.read_bytes()))
    rec["HostCertificate"] = _ensure_pem(rec["HostCertificate"], "CERTIFICATE")
    rec["HostPrivateKey"] = _ensure_pem(rec["HostPrivateKey"], "PRIVATE KEY")
    return rec


def _load_prev() -> dict[str, Any]:
    try:
        if OUT.exists():
            return json.loads(OUT.read_text())
    except Exception:
        pass
    return {}


async def _refresh_device_host(
    dev: dict[str, Any], *, attempts: int = 2, delay: float = 0.5
) -> bool:
    """Resolve current Wi‑Fi IP from Bonjour; update dev['host'] when it changes."""
    from pair_service import _host_rank, discover_wifi_host_async

    host = str(dev.get("host") or "")
    # Stored IPv4: quick Bonjour probe only (device may be asleep → keep IP)
    if _host_rank(host) == 0 and attempts > 2:
        attempts = 2
    try:
        better = await discover_wifi_host_async(str(dev["udid"]), attempts=attempts, delay=delay)
    except Exception as e:
        print(f"[poll] host refresh {dev['udid'][:8]}: {e}", flush=True)
        return False
    if not better or better == host:
        return False
    print(f"[poll] host {host!r} → {better!r} for {dev['udid'][:8]}", flush=True)
    dev["host"] = better
    try:
        store = load_store()
        changed = False
        for d in store.get("devices") or []:
            if d.get("udid") == dev.get("udid"):
                d["host"] = better
                changed = True
        if changed:
            save_store(store)
    except Exception:
        pass
    return True


async def _device_battery_live(
    rec: dict[str, Any], host: str, udid: str, dev: dict[str, Any]
) -> dict[str, Any]:
    """Lockdown device read with Bonjour host refresh + one retry on timeout."""
    last_err: Exception | None = None
    use_host = host
    for attempt in range(2):
        try:
            return await asyncio.wait_for(
                _device_battery(rec, host=use_host, udid=udid),
                timeout=LOCKDOWN_TIMEOUT,
            )
        except Exception as e:
            last_err = e
            if attempt == 0 and isinstance(e, (asyncio.TimeoutError, TimeoutError, OSError)):
                if await _refresh_device_host(dev, attempts=3, delay=1.0):
                    use_host = str(dev["host"])
                    print(f"[poll] retry lockdown {udid[:8]} @ {use_host}", flush=True)
                    continue
            break
    assert last_err is not None
    raise last_err


async def _device_battery(rec: dict[str, Any], host: str | None = None, udid: str | None = None) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_tcp

    ld = await create_using_tcp(
        hostname=host or HOST,
        identifier=udid or UDID,
        autopair=False,
        pair_record=rec,
        pairing_records_cache_folder=LOCKDOWN_DIR,
        keep_alive=True,
    )
    try:
        batt = await ld.get_value(domain="com.apple.mobile.battery")
        pct = batt.get("BatteryCurrentCapacity")
        full = bool(batt.get("FullyCharged"))
        charging = bool(batt.get("BatteryIsCharging"))
        plugged = bool(batt.get("ExternalConnected"))
        if full:
            state = "full"
        elif charging or plugged:
            state = "charging"
        else:
            state = "Not Charging"
        name = await ld.get_value(key="DeviceName")
        product = await ld.get_value(key="ProductType")
        return {
            "role": "device",
            "kind": classify_kind(product, udid or UDID),
            "battery_level": int(pct) if pct is not None else None,
            "battery_state": state,
            "name": name,
            "product_type": product,
            "raw": batt,
        }
    finally:
        await ld.close()


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
    """Copy remote_*.plist to share so reinstalls keep Watch path."""
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
        from pymobiledevice3.bonjour import browse_remotepairing

        answers = await browse_remotepairing()
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

    try:
        Path("/share/idevice_diag.json").write_text(json.dumps(diag, indent=2, default=str))
    except Exception:
        pass
    print(
        f"[diag] remote_plist={diag['remote_plist_exists']} "
        f"files={diag['remote_files']} bonjour={diag['bonjour_services']}",
        flush=True,
    )
    return diag


async def _browse_remotepairing_services(udid: str, attempts: int = 4, delay: float = 1.2):
    """Retry Bonjour browse — RemotePairing often appears a few seconds after wake."""
    from pymobiledevice3.remote.tunnel_service import get_remote_pairing_tunnel_services

    if not has_remote_pair_record(udid):
        print(
            f"REMOTE_PAIR_MISSING {udid[:8]}… "
            f"expected {remote_pair_path(udid)} — USB: + Add wizard (RemotePairing)",
            flush=True,
        )
        return []

    last: list[Any] = []
    for i in range(max(1, attempts)):
        try:
            last = list(await get_remote_pairing_tunnel_services(udid=udid) or [])
        except Exception as e:
            print(f"REMOTEPAIRING browse {i + 1}/{attempts}: {e}", flush=True)
            last = []
        if last:
            return last
        if i + 1 < attempts:
            await asyncio.sleep(delay)
    return last


async def _accessories_via_remotepairing(
    host: str | None = None, udid: str | None = None
) -> dict[str, Any]:
    """List accessories (Watch, AirPods, …) exposed by a paired device via CompanionProxy."""
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane, UserspaceTun
    from pymobiledevice3.services.companion import CompanionProxyService

    use_udid = udid or UDID
    use_host = host or HOST
    result: dict[str, Any] = {"accessories": [], "error": None}

    tunnel_service.USE_USERSPACE_TUNNEL = True
    tunnel_service.RemotePairingTcpTunnel.REQUESTED_MTU = DEFAULT_MTU
    stack = AsyncExitStack()
    try:
        if not has_remote_pair_record(use_udid):
            await diagnose_companion_async(use_udid, use_host)
            raise RuntimeError(
                "RemotePairing record missing — connect the device by USB and run + Add. "
                "Lockdown Trust alone is not enough for accessories."
            )
        services = await _browse_remotepairing_services(use_udid)
        if not services:
            await diagnose_companion_async(use_udid, use_host)
            raise RuntimeError(
                "no RemotePairing on Bonjour; unlock the device and keep Wi‑Fi on"
            )
        provider = next(
            (s for s in services if getattr(s, "hostname", None) == use_host),
            services[0],
        )
        print(
            f"REMOTEPAIRING host={getattr(provider, 'hostname', '?')} "
            f"port={getattr(provider, 'port', '?')} n={len(services)}",
            flush=True,
        )
        stack.push_async_callback(provider.close)
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

        companion = CompanionProxyService(rsd)
        listed = await companion.list()
        print(f"COMPANION_LIST {listed}", flush=True)
        if not listed:
            result["error"] = "no accessories on the last scan"
            return result

        accessories: list[dict[str, Any]] = []
        for item in listed:
            dev_udid = _companion_item_udid(item)
            info = await _fetch_companion_device(companion, dev_udid)
            if not info:
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
            if info.get("kind") == "watch":
                try:
                    backup_remote_pair_records()
                except Exception:
                    pass

        result["accessories"] = accessories
        if not accessories:
            result["error"] = "no accessories on the last scan"
        return result
    finally:
        await stack.aclose()
        tunnel_service.USE_USERSPACE_TUNNEL = False


async def fetch_device(dev: dict[str, Any], prev_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    global UDID, HOST
    udid = dev["udid"]
    host = dev["host"]
    UDID, HOST = udid, host
    prev_entry = prev_entry or {}
    prev_hub = prev_entry.get("hub") or prev_entry.get("phone") or {}
    prev_level = prev_entry.get("battery_level")
    if prev_level is None:
        prev_level = prev_hub.get("battery_level")
    prev_state = prev_entry.get("battery_state")
    if prev_state is None:
        prev_state = prev_hub.get("battery_state")
    prev_stale = prev_entry.get("stale")
    if prev_stale is None:
        prev_stale = bool(prev_entry.get("hub_stale"))
    prev_acc = accessories_from_entry(prev_entry)
    name = dev.get("name") or prev_entry.get("name") or prev_hub.get("name")
    product = dev.get("product_type") or prev_entry.get("product_type") or prev_hub.get("product_type")
    entry: dict[str, Any] = {
        "udid": udid,
        "host": host,
        "name": name,
        "product_type": product,
        "role": "device",
        "kind": classify_kind(product, udid),
        "battery_level": prev_level,
        "battery_state": prev_state,
        "raw": prev_entry.get("raw") if "raw" in prev_entry else prev_hub.get("raw"),
        "stale": bool(prev_stale),
        "updated_at": prev_entry.get("updated_at") or prev_entry.get("hub_updated_at"),
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
        entry["battery_level"] = live.get("battery_level")
        entry["battery_state"] = live.get("battery_state")
        entry["raw"] = live.get("raw")
        entry["name"] = live.get("name") or entry["name"]
        entry["product_type"] = live.get("product_type") or entry["product_type"]
        entry["kind"] = classify_kind(entry["product_type"], udid)
        entry["stale"] = False
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            from devices_store import load_store, save_store

            store = load_store()
            changed = False
            for d in store.get("devices") or []:
                if d.get("udid") == udid:
                    if entry["name"] and d.get("name") != entry["name"]:
                        d["name"] = entry["name"]
                        changed = True
                    if entry["product_type"] and d.get("product_type") != entry["product_type"]:
                        d["product_type"] = entry["product_type"]
                        changed = True
            if changed:
                save_store(store)
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
    try:
        scan = await _accessories_via_remotepairing(host=host, udid=udid)
        found = scan.get("accessories") or []
        if found:
            ts = datetime.now(timezone.utc).isoformat()
            entry["accessories"] = [
                {**a, "stale": False, "updated_at": ts} for a in found
            ]
            accessories_ok = True
        if scan.get("error") and not found:
            errors.append(f"accessories: {scan['error']}")
            print(f"ACCESSORY_FAIL {scan['error']}", flush=True)
        elif scan.get("error"):
            print(f"ACCESSORY_NOTE {scan['error']}", flush=True)
    except Exception as e:
        errors.append(f"accessories: {type(e).__name__}: {e}")
        print(f"ACCESSORY_FAIL {type(e).__name__}: {e}", flush=True)

    if not accessories_ok:
        entry["accessories"] = mark_accessories_stale(prev_acc)

    if errors:
        if not device_ok:
            entry["error"] = "; ".join(errors)
        else:
            entry["accessories_error"] = "; ".join(
                e for e in errors if e.startswith("accessories:")
            ) or None
    apply_legacy_aliases(entry)
    return entry


# Back-compat for older call sites
fetch_hub = fetch_device


async def fetch_once() -> dict[str, Any]:
    store = load_store()
    devices = list(store.get("devices") or [])
    # Refresh Wi‑Fi IPs from Bonjour before each poll (parallel; keep stored IPv4 if asleep)
    if devices:
        await asyncio.gather(
            *[_refresh_device_host(dev, attempts=2, delay=0.5) for dev in devices]
        )

    prev = _load_prev()
    prev_by = {d.get("udid"): d for d in (prev.get("devices") or []) if d.get("udid")}

    # Legacy single-device prev
    if not prev_by and prev.get("phone_udid"):
        prev_by[prev["phone_udid"]] = {
            "hub": prev.get("phone"),
            "phone": prev.get("phone"),
            "watch": prev.get("watch"),
        }

    doc: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": "remotepairing-userspace-rsd",
        "devices": [],
        "phone_udid": None,
        "phone": prev.get("phone"),
        "watch": prev.get("watch"),
        "error": None,
    }

    if not devices:
        # Fallback env (legacy)
        if UDID and HOST:
            devices = [{"udid": UDID, "host": HOST, "name": UDID[:8]}]
        else:
            doc["error"] = "no paired devices — open the add-on UI and tap Add"
            return doc

    errors: list[str] = []
    entries = await asyncio.gather(
        *[fetch_device(dev, prev_by.get(dev["udid"])) for dev in devices]
    )
    for dev, entry in zip(devices, entries):
        doc["devices"].append(entry)
        if entry.get("error"):
            errors.append(f"{dev['udid'][:8]}: {entry['error']}")

    doc.update(snapshot_root(doc["devices"], prev))
    if errors:
        doc["error"] = "; ".join(errors)
    return doc


def _write(doc: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(OUT)
    try:
        from mqtt_ha import sync_battery_doc

        sync_battery_doc(doc)
    except Exception as e:
        print(f"[mqtt] poll sync failed: {e}", flush=True)


async def loop() -> None:
    while True:
        store = load_store()
        poll = int(store.get("poll_seconds") or os.environ.get("IDEVICE_POLL_SEC") or 180)
        try:
            doc = await fetch_once()
        except Exception as e:
            prev = _load_prev()
            doc = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "phone_udid": prev.get("phone_udid"),
                "phone": prev.get("phone"),
                "watch": prev.get("watch"),
                "devices": prev.get("devices") or [],
                "path": "remotepairing-userspace-rsd",
                "error": f"{type(e).__name__}: {e}",
            }
            print("FETCH_FAIL", doc["error"], flush=True)
            traceback.print_exc()
        _write(doc)
        await asyncio.sleep(poll)


def main() -> int:
    # Prefer devices.json; allow env override for one-shot tools
    prim = primary_device()
    global UDID, HOST
    if prim:
        UDID = prim["udid"]
        HOST = prim["host"]
    if "--once" in sys.argv:
        if not (UDID and HOST) and not (load_store().get("devices")):
            print("No paired devices", flush=True)
            return 1
        doc = asyncio.run(fetch_once())
        print(json.dumps(doc, indent=2, default=str))
        _write(doc)
        return 0 if doc.get("phone") or doc.get("devices") else 1
    asyncio.run(loop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
