#!/usr/bin/env python3
"""Device registry for iDevice Battery add-on."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA = Path(os.environ.get("IDEVICE_DATA", "/data"))
DEVICES_PATH = DATA / "devices.json"
OPTS_PATH = DATA / "options.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store() -> dict[str, Any]:
    return {"poll_seconds": 180, "devices": []}


def _poll_seconds_from_opts(opts: dict[str, Any]) -> int | None:
    """Configuration: poll_minutes 1–10 (legacy: poll_seconds)."""
    if opts.get("poll_minutes") is not None:
        try:
            minutes = int(opts["poll_minutes"])
            return max(1, min(10, minutes)) * 60
        except (TypeError, ValueError):
            return None
    if opts.get("poll_seconds") is not None:
        try:
            sec = int(opts["poll_seconds"])
            minutes = max(1, min(10, round(sec / 60) or 1))
            return minutes * 60
        except (TypeError, ValueError):
            return None
    return None


def load_store() -> dict[str, Any]:
    store = default_store()
    if DEVICES_PATH.exists():
        try:
            raw = json.loads(DEVICES_PATH.read_text())
            if isinstance(raw, dict):
                store["poll_seconds"] = int(raw.get("poll_seconds") or 180)
                store["devices"] = list(raw.get("devices") or [])
        except Exception:
            pass
    # Seed from legacy add-on options (one-time if empty)
    if not store["devices"] and OPTS_PATH.exists():
        try:
            opts = json.loads(OPTS_PATH.read_text())
            udid = (opts.get("phone_udid") or "").strip()
            host = (opts.get("phone_host") or "").strip()
            if udid and host:
                store["devices"].append(
                    {
                        "udid": udid,
                        "host": host,
                        "name": udid[:8],
                        "product_type": "",
                        "added_at": _now(),
                    }
                )
            poll = _poll_seconds_from_opts(opts)
            if poll is not None:
                store["poll_seconds"] = poll
            save_store(store)
        except Exception:
            pass
    elif OPTS_PATH.exists():
        try:
            opts = json.loads(OPTS_PATH.read_text())
            poll = _poll_seconds_from_opts(opts)
            if poll is not None:
                store["poll_seconds"] = poll
        except Exception:
            pass
    return store


def save_store(store: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(DEVICES_PATH)


def upsert_device(device: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    udid = device["udid"]
    devices = [d for d in store["devices"] if d.get("udid") != udid]
    device = dict(device)
    device.setdefault("added_at", _now())
    devices.append(device)
    store["devices"] = devices
    save_store(store)
    return store


def remove_device(udid: str) -> dict[str, Any]:
    store = load_store()
    store["devices"] = [d for d in store["devices"] if d.get("udid") != udid]
    save_store(store)
    return store


def primary_device() -> dict[str, Any] | None:
    devices = load_store().get("devices") or []
    return devices[0] if devices else None
