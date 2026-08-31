#!/usr/bin/env python3
"""Phone + Watch over Wi-Fi (AirBattery-equivalent values on Linux).

Phone: lockdown TCP :62078 + pair record → com.apple.mobile.battery
Watch: RemotePairing → userspace CDTunnel → RSD → companion_proxy

Reads paired hubs from /data/devices.json (see devices_store.py).
Keeps top-level phone/watch fields for the primary hub (HA sensors).
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

OUT = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
LOCKDOWN_DIR = Path(os.environ.get("IDEVICE_LOCKDOWN", "/var/lib/lockdown"))
DEFAULT_MTU = int(os.environ.get("IDEVICE_CDTUNNEL_MTU", "16000"))

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


async def _phone_battery(rec: dict[str, Any], host: str | None = None, udid: str | None = None) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_tcp

    phone_ld = await create_using_tcp(
        hostname=host or HOST,
        identifier=udid or UDID,
        autopair=False,
        pair_record=rec,
        pairing_records_cache_folder=LOCKDOWN_DIR,
        keep_alive=True,
    )
    try:
        batt = await phone_ld.get_value(domain="com.apple.mobile.battery")
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
        name = await phone_ld.get_value(key="DeviceName")
        product = await phone_ld.get_value(key="ProductType")
        return {
            "battery_level": int(pct) if pct is not None else None,
            "battery_state": state,
            "name": name,
            "product_type": product,
            "raw": batt,
        }
    finally:
        await phone_ld.close()


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
    return {
        "udid": device_udid,
        "name": name,
        "product_type": product,
        "battery_level": level,
        "battery_state": state,
    }


def _companion_item_udid(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("UDID") or item.get("udid") or item)
    return str(item)


def _is_watch_device(info: dict[str, Any]) -> bool:
    product = str(info.get("product_type") or "")
    udid = str(info.get("udid") or "")
    if product.startswith("Watch"):
        return True
    return udid.startswith("00008310")


async def _companion_via_remotepairing(
    host: str | None = None, udid: str | None = None
) -> dict[str, Any]:
    """List all companion devices (Watch, AirPods, …) exposed by the hub."""
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.remote.tunnel_service import get_remote_pairing_tunnel_services
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane, UserspaceTun
    from pymobiledevice3.services.companion import CompanionProxyService

    use_udid = udid or UDID
    use_host = host or HOST
    result: dict[str, Any] = {"watch": None, "accessories": [], "error": None}

    tunnel_service.USE_USERSPACE_TUNNEL = True
    tunnel_service.RemotePairingTcpTunnel.REQUESTED_MTU = DEFAULT_MTU
    stack = AsyncExitStack()
    try:
        services = await get_remote_pairing_tunnel_services(udid=use_udid)
        if not services:
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

        watch: dict[str, Any] | None = None
        accessories: list[dict[str, Any]] = []
        for item in listed:
            dev_udid = _companion_item_udid(item)
            info = await _fetch_companion_device(companion, dev_udid)
            if not info:
                continue
            if info.get("battery_level") is None:
                print(f"COMPANION_SKIP {dev_udid[:8]} no battery", flush=True)
                continue
            if _is_watch_device(info) and watch is None:
                watch = info
                print(
                    f"WATCH_OK {info['battery_level']}% {info['battery_state']}",
                    flush=True,
                )
            else:
                accessories.append(info)
                print(
                    f"ACCESSORY_OK {info.get('name')} {info['battery_level']}%",
                    flush=True,
                )

        result["watch"] = watch
        result["accessories"] = accessories
        if not watch and not accessories:
            result["error"] = "no accessories on the last scan"
        return result
    finally:
        await stack.aclose()
        tunnel_service.USE_USERSPACE_TUNNEL = False


async def fetch_hub(dev: dict[str, Any], prev_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    global UDID, HOST
    udid = dev["udid"]
    host = dev["host"]
    UDID, HOST = udid, host
    prev_entry = prev_entry or {}
    entry: dict[str, Any] = {
        "udid": udid,
        "host": host,
        "name": dev.get("name"),
        "product_type": dev.get("product_type"),
        "hub": prev_entry.get("hub") or prev_entry.get("phone"),
        "watch": prev_entry.get("watch"),
        "accessories": prev_entry.get("accessories") or [],
        "error": None,
    }
    errors: list[str] = []
    try:
        rec = load_pair_record(udid)
        entry["hub"] = await _phone_battery(rec, host=host, udid=udid)
        # refresh friendly name
        entry["name"] = entry["hub"].get("name") or entry["name"]
        entry["product_type"] = entry["hub"].get("product_type") or entry["product_type"]
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
            f"PHONE_OK {udid[:8]}… {entry['hub']['battery_level']}% {entry['hub']['battery_state']}",
            flush=True,
        )
    except Exception as e:
        errors.append(f"hub: {type(e).__name__}: {e}")
        print(f"PHONE_FAIL {type(e).__name__}: {e}", flush=True)

    try:
        comp = await _companion_via_remotepairing(host=host, udid=udid)
        if comp.get("watch"):
            entry["watch"] = comp["watch"]
        if comp.get("accessories"):
            entry["accessories"] = comp["accessories"]
        if comp.get("error") and not comp.get("watch") and not comp.get("accessories"):
            errors.append(f"accessories: {comp['error']}")
            print(f"COMPANION_FAIL {comp['error']}", flush=True)
        elif comp.get("error"):
            # Hub reachable; accessories partial — keep data, soft note only
            print(f"COMPANION_NOTE {comp['error']}", flush=True)
    except Exception as e:
        errors.append(f"accessories: {type(e).__name__}: {e}")
        print(f"COMPANION_FAIL {type(e).__name__}: {e}", flush=True)

    # Hub reachability drives card status; accessory issues are non-fatal
    if errors:
        hub_failed = not (entry.get("hub") and entry["hub"].get("battery_level") is not None)
        if hub_failed:
            entry["error"] = "; ".join(errors)
        else:
            entry["accessory_note"] = "; ".join(
                e for e in errors if e.startswith("accessories:")
            ) or None
    return entry


async def fetch_once() -> dict[str, Any]:
    store = load_store()
    devices = store.get("devices") or []
    # Upgrade link-local / unusable hosts to a real Bonjour Wi‑Fi address (IPv4 preferred)
    try:
        from pair_service import _host_rank, wifi_host_from_bonjour_async

        changed = False
        for dev in devices:
            host = str(dev.get("host") or "")
            rank = _host_rank(host)
            # Already plain IPv4 — keep. Upgrade fe80 / .local / IPv6 if Bonjour has better.
            if rank == 0:
                continue
            better = await wifi_host_from_bonjour_async(str(dev["udid"]))
            if better and _host_rank(better) < rank:
                print(f"[poll] host {host!r} → {better!r} for {dev['udid'][:8]}", flush=True)
                dev["host"] = better
                changed = True
        if changed:
            save_store(store)
    except Exception as e:
        print(f"[poll] wifi_host refresh skipped: {e}", flush=True)

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
    for dev in devices:
        entry = await fetch_hub(dev, prev_by.get(dev["udid"]))
        doc["devices"].append(entry)
        if entry.get("error"):
            errors.append(f"{dev['udid'][:8]}: {entry['error']}")

    # Primary hub → legacy phone/watch keys for existing HA sensors
    primary = doc["devices"][0]
    doc["phone_udid"] = primary.get("udid")
    if primary.get("hub"):
        doc["phone"] = primary["hub"]
    if primary.get("watch"):
        doc["watch"] = primary["watch"]
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
