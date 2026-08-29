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


async def _pair_lockdown(udid: Optional[str] = None) -> dict[str, Any]:
    from pymobiledevice3.lockdown import create_using_usbmux

    _set_job(
        state="need_trust",
        phase="trust",
        message="Confirm Trust on the device if asked, then wait…",
        error=None,
    )
    ld = await create_using_usbmux(
        serial=udid,
        autopair=True,
        pairing_records_cache_folder=LOCKDOWN_DIR,
    )
    try:
        info = {
            "udid": ld.udid,
            "name": await ld.get_value(key="DeviceName"),
            "product_type": await ld.get_value(key="ProductType"),
        }
    finally:
        await ld.close()
    return info


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
        raise RuntimeError(err)


def _guess_host_from_bonjour(udid: str) -> str:
    try:
        from pymobiledevice3.remote.tunnel_service import get_remote_pairing_tunnel_services

        services = asyncio.run(get_remote_pairing_tunnel_services(udid=udid))
        for s in services or []:
            host = getattr(s, "hostname", None)
            if host and ":" not in str(host):  # prefer IPv4-looking
                return str(host)
            if host:
                return str(host)
    except Exception:
        pass
    return ""


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
        _set_job(
            state="need_trust",
            phase="trust",
            message="A Trust notification will appear on the device. Tap Trust.",
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
        host = _guess_host_from_bonjour(info["udid"])
        info["host_guess"] = host
        _set_job(
            state="ok",
            phase="ready",
            message="Pairing complete. Confirm Wi‑Fi address, then finish.",
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


def start_pair_async(udid: Optional[str] = None) -> dict[str, Any]:
    with _job_lock:
        if _job.get("state") in ("running", "need_trust"):
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
    _set_job(state="idle", phase="", message="Device saved.", device=entry, error=None)
    return {"device": entry, "store": store}


def verify_device(udid: str, host: str) -> dict[str, Any]:
    """One-shot poll for wizard Verify step."""
    os.environ["IDEVICE_UDID"] = udid
    os.environ["IDEVICE_HOST"] = host
    # Import fresh-ish helpers from poller
    import rsd_battery as rb

    rb.UDID = udid
    rb.HOST = host

    async def _run() -> dict[str, Any]:
        result: dict[str, Any] = {
            "hub": None,
            "watch": None,
            "accessories": [],
            "error": None,
        }
        errors: list[str] = []
        try:
            rec = rb.load_pair_record(udid)
            result["hub"] = await rb._phone_battery(rec, host=host, udid=udid)
        except Exception as e:
            errors.append(f"hub: {e}")
        try:
            result["watch"] = await rb._watch_via_remotepairing(host=host, udid=udid)
        except Exception as e:
            errors.append(f"watch: {e}")
        if errors:
            result["error"] = "; ".join(errors)
        return result

    return asyncio.run(_run())
