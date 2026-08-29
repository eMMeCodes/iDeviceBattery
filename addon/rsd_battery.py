#!/usr/bin/env python3
"""Phone + Watch over Wi-Fi (AirBattery-equivalent values on Linux).

Phone: lockdown TCP :62078 + pair record → com.apple.mobile.battery
Watch: RemotePairing (pair once via USB) → userspace CDTunnel → RSD
       → com.apple.companion_proxy.shim.remote → BatteryCurrentCapacity

Pair records:
  /var/lib/lockdown/<UDID>.plist          (lockdown Trust)
  ~/.pymobiledevice3/remote_<UDID>.plist  (RemotePairing; HOME=/data)
"""
from __future__ import annotations

import asyncio
import json
import os
import plistlib
import sys
import traceback
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, cast

UDID = os.environ.get("IDEVICE_UDID", "").strip()
HOST = os.environ.get("IDEVICE_HOST", "").strip()
POLL = int(os.environ.get("IDEVICE_POLL_SEC", "120"))
if not UDID or not HOST:
    raise SystemExit("IDEVICE_UDID and IDEVICE_HOST are required")
OUT = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
LOCKDOWN_DIR = Path(os.environ.get("IDEVICE_LOCKDOWN", "/var/lib/lockdown"))
DEFAULT_MTU = int(os.environ.get("IDEVICE_CDTUNNEL_MTU", "16000"))


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


def load_pair_record() -> dict[str, Any]:
    plist_path = LOCKDOWN_DIR / f"{UDID}.plist"
    if not plist_path.exists():
        raise FileNotFoundError(f"missing pair record {plist_path}")
    rec = dict(plistlib.loads(plist_path.read_bytes()))
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


async def _phone_battery(rec: dict[str, Any]) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_tcp

    phone_ld = await create_using_tcp(
        hostname=HOST,
        identifier=UDID,
        autopair=False,
        pair_record=rec,
        pairing_records_cache_folder=LOCKDOWN_DIR,
        keep_alive=True,
    )
    try:
        batt = await phone_ld.get_value(domain="com.apple.mobile.battery")
        pct = batt.get("BatteryCurrentCapacity")
        # BatteryIsCharging=false with ExternalConnected=true is normal during
        # Optimized Battery Charging (~80% hold). Treat plugged-in as charging
        # so HA matches "on charger" UX (Companion-style).
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


async def _watch_via_remotepairing() -> dict[str, Any]:
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.remote.tunnel_service import get_remote_pairing_tunnel_services
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane, UserspaceTun
    from pymobiledevice3.services.companion import CompanionProxyService

    tunnel_service.USE_USERSPACE_TUNNEL = True
    tunnel_service.RemotePairingTcpTunnel.REQUESTED_MTU = DEFAULT_MTU
    stack = AsyncExitStack()
    try:
        services = await get_remote_pairing_tunnel_services(udid=UDID)
        if not services:
            raise RuntimeError(
                "no RemotePairing on Bonjour; USB once: "
                "pymobiledevice3 lockdown remotepairing --pair"
            )
        # Prefer IPv4 matching HOST; else first service
        provider = next(
            (s for s in services if getattr(s, "hostname", None) == HOST),
            services[0],
        )
        print(
            f"REMOTEPAIRING host={getattr(provider, 'hostname', '?')} "
            f"port={getattr(provider, 'port', '?')} n={len(services)}",
            flush=True,
        )
        stack.push_async_callback(provider.close)
        tunnel_result = await stack.enter_async_context(provider.start_tcp_tunnel())
        print(
            f"TUNNEL_OK {tunnel_result.address} rsd={tunnel_result.port}",
            flush=True,
        )
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
        watches = await companion.list()
        print(f"WATCH_LIST {watches}", flush=True)
        if not watches:
            raise RuntimeError("no paired watches in companion registry")

        watch_udid = watches[0]
        if isinstance(watch_udid, dict):
            watch_udid = watch_udid.get("UDID") or str(watch_udid)
        watch_udid = str(watch_udid)

        name = product = level = is_charging = None
        for key in (
            "DeviceName",
            "ProductType",
            "BatteryCurrentCapacity",
            "BatteryIsCharging",
        ):
            try:
                val = await companion.get_value(watch_udid, key)
            except Exception as e:
                print(f"WATCH_{key}_FAIL {e}", flush=True)
                continue
            if isinstance(val, dict) and key in val:
                val = val[key]
            print(f"WATCH_{key} {val}", flush=True)
            if key == "DeviceName":
                name = val
            elif key == "ProductType":
                product = val
            elif key == "BatteryCurrentCapacity":
                level = int(val) if val is not None else None
            elif key == "BatteryIsCharging":
                is_charging = bool(val)

        if level is None:
            raise RuntimeError("Watch battery level missing")

        return {
            "udid": watch_udid,
            "name": name,
            "product_type": product,
            "battery_level": level,
            "battery_state": "charging" if is_charging else "Not Charging",
        }
    finally:
        await stack.aclose()
        tunnel_service.USE_USERSPACE_TUNNEL = False


async def fetch_once() -> dict[str, Any]:
    prev = _load_prev()
    doc: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phone_udid": UDID,
        "phone": prev.get("phone"),
        "watch": prev.get("watch"),
        "path": "remotepairing-userspace-rsd",
        "error": None,
    }
    errors: list[str] = []

    try:
        rec = load_pair_record()
        doc["phone"] = await _phone_battery(rec)
        print(
            f"PHONE_OK {doc['phone']['battery_level']}% {doc['phone']['battery_state']}",
            flush=True,
        )
    except Exception as e:
        errors.append(f"phone: {type(e).__name__}: {e}")
        print(f"PHONE_FAIL {type(e).__name__}: {e}", flush=True)

    try:
        doc["watch"] = await _watch_via_remotepairing()
        print(
            f"WATCH_OK {doc['watch']['battery_level']}% {doc['watch']['battery_state']}",
            flush=True,
        )
    except Exception as e:
        errors.append(f"watch: {type(e).__name__}: {e}")
        print(f"WATCH_FAIL {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    if errors:
        doc["error"] = "; ".join(errors)
    return doc


def _write(doc: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(OUT)


async def loop() -> None:
    while True:
        try:
            doc = await fetch_once()
        except Exception as e:
            doc = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "phone_udid": UDID,
                "phone": _load_prev().get("phone"),
                "watch": _load_prev().get("watch"),
                "path": "remotepairing-userspace-rsd",
                "error": f"{type(e).__name__}: {e}",
            }
            print("FETCH_FAIL", doc["error"], flush=True)
            traceback.print_exc()
        _write(doc)
        await asyncio.sleep(POLL)


def main() -> int:
    if "--once" in sys.argv:
        doc = asyncio.run(fetch_once())
        print(json.dumps(doc, indent=2, default=str))
        _write(doc)
        return 0 if doc.get("watch") or doc.get("phone") else 1
    asyncio.run(loop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
