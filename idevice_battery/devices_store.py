#!/usr/bin/env python3
"""Device registry for iDevice Battery add-on."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DATA = Path(os.environ.get("IDEVICE_DATA", "/data"))
DEVICES_PATH = DATA / "devices.json"
OPTS_PATH = DATA / "options.json"
LOCK_PATH = DATA / "devices.lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store() -> dict[str, Any]:
    return {"poll_seconds": 180, "devices": []}


def _poll_seconds_from_opts(opts: dict[str, Any]) -> int | None:
    """Configuration: poll_minutes 1–10."""
    if opts.get("poll_minutes") is not None:
        try:
            minutes = int(opts["poll_minutes"])
            return max(1, min(10, minutes)) * 60
        except (TypeError, ValueError):
            return None
    return None


@contextmanager
def _lock() -> Iterator[None]:
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read_store_unlocked() -> dict[str, Any]:
    store = default_store()
    if DEVICES_PATH.exists():
        try:
            raw = json.loads(DEVICES_PATH.read_text())
            if isinstance(raw, dict):
                store["poll_seconds"] = int(raw.get("poll_seconds") or 180)
                store["devices"] = list(raw.get("devices") or [])
        except Exception:
            pass
    if OPTS_PATH.exists():
        try:
            opts = json.loads(OPTS_PATH.read_text())
            poll = _poll_seconds_from_opts(opts)
            if poll is not None:
                store["poll_seconds"] = poll
        except Exception:
            pass
    return store


def _write_store_unlocked(store: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(DEVICES_PATH)


def load_store() -> dict[str, Any]:
    with _lock():
        return _read_store_unlocked()


def save_store(store: dict[str, Any]) -> None:
    with _lock():
        _write_store_unlocked(store)


def registered_udids() -> set[str]:
    return {str(d.get("udid")) for d in load_store().get("devices") or [] if d.get("udid")}


def patch_device(udid: str, **fields: Any) -> bool:
    """Update fields on a registry row. No-op (False) if the UDID was removed."""
    with _lock():
        store = _read_store_unlocked()
        found = False
        for d in store.get("devices") or []:
            if d.get("udid") != udid:
                continue
            for key, value in fields.items():
                if value is not None:
                    d[key] = value
            found = True
            break
        if found:
            _write_store_unlocked(store)
        return found


def upsert_device(device: dict[str, Any]) -> dict[str, Any]:
    with _lock():
        store = _read_store_unlocked()
        udid = device["udid"]
        devices = [d for d in store["devices"] if d.get("udid") != udid]
        device = dict(device)
        device.setdefault("added_at", _now())
        devices.append(device)
        store["devices"] = devices
        _write_store_unlocked(store)
        return store


def remove_device(udid: str) -> dict[str, Any]:
    with _lock():
        store = _read_store_unlocked()
        store["devices"] = [d for d in store["devices"] if d.get("udid") != udid]
        _write_store_unlocked(store)
        return store


def primary_device() -> dict[str, Any] | None:
    devices = load_store().get("devices") or []
    return devices[0] if devices else None
