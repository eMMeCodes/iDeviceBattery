"""Shared battery model: devices vs accessories, then a product kind."""
from __future__ import annotations

from typing import Any


KIND_LABELS = {
    "iphone": "iPhone",
    "ipad": "iPad",
    "ipod": "iPod",
    "watch": "Watch",
    "airpods": "AirPods",
    "headphones": "Headphones",
    "pencil": "Pencil",
    "keyboard": "Keyboard",
    "trackpad": "Trackpad",
    "mac": "Mac",
    "accessory": "Accessory",
    "device": "Device",
}


def classify_kind(product_type: str | None = None, udid: str | None = None) -> str:
    """Map Apple ProductType / UDID prefix to a stable kind."""
    p = str(product_type or "")
    u = str(udid or "")
    if p.startswith("Watch") or u.startswith("00008310"):
        return "watch"
    if p.startswith("iPhone"):
        return "iphone"
    if p.startswith("iPad"):
        return "ipad"
    if p.startswith("iPod"):
        return "ipod"
    if p.startswith("AirPods") or p.startswith("iProd") or "AirPods" in p:
        return "airpods"
    if p.startswith("Beats") or "Headphone" in p:
        return "headphones"
    if "Pencil" in p:
        return "pencil"
    if "Keyboard" in p:
        return "keyboard"
    if "Trackpad" in p:
        return "trackpad"
    if p.startswith("Mac") or p.startswith("iMac"):
        return "mac"
    if p:
        return "accessory"
    return "device"


def kind_label(kind: str | None, fallback: str = "") -> str:
    if kind and kind in KIND_LABELS:
        return KIND_LABELS[kind]
    return fallback or (kind or "Device")


def normalize_accessory(
    raw: dict[str, Any] | None,
    *,
    stale: bool = False,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    if not raw:
        return None
    kind = raw.get("kind") or classify_kind(raw.get("product_type"), raw.get("udid"))
    return {
        "role": "accessory",
        "kind": kind,
        "udid": raw.get("udid") or "",
        "name": raw.get("name"),
        "product_type": raw.get("product_type"),
        "battery_level": raw.get("battery_level"),
        "battery_state": raw.get("battery_state"),
        "stale": bool(raw["stale"] if "stale" in raw else stale),
        "updated_at": raw.get("updated_at") or updated_at,
    }


def accessories_from_entry(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """One accessory list. Legacy `watch` is merged in as kind=watch."""
    entry = entry or {}
    stale_fb = bool(entry.get("watch_stale") or entry.get("accessories_stale"))
    updated_fb = entry.get("watch_updated_at") or entry.get("accessories_updated_at")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(raw: dict[str, Any] | None, *, front: bool = False) -> None:
        acc = normalize_accessory(raw, stale=stale_fb, updated_at=updated_fb)
        if not acc:
            return
        key = str(acc.get("udid") or acc.get("name") or "")
        if not key or key in seen:
            return
        seen.add(key)
        if front:
            out.insert(0, acc)
        else:
            out.append(acc)

    for raw in entry.get("accessories") or []:
        if isinstance(raw, dict):
            _add(raw)
    watch = entry.get("watch")
    if isinstance(watch, dict):
        _add(watch, front=True)
    return out


def first_of_kind(items: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((a for a in items if a.get("kind") == kind), None)


def device_battery(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical device battery view (reads flattened fields or legacy hub/phone)."""
    entry = entry or {}
    hub = entry.get("hub") or entry.get("phone") or {}
    level = entry.get("battery_level")
    if level is None:
        level = hub.get("battery_level")
    state = entry.get("battery_state")
    if state is None:
        state = hub.get("battery_state")
    name = entry.get("name") or hub.get("name")
    product = entry.get("product_type") or hub.get("product_type")
    stale = entry.get("stale")
    if stale is None:
        stale = bool(entry.get("hub_stale"))
    return {
        "role": "device",
        "kind": entry.get("kind") or classify_kind(product, entry.get("udid")),
        "udid": entry.get("udid") or hub.get("udid") or "",
        "name": name,
        "product_type": product,
        "battery_level": level,
        "battery_state": state,
        "raw": entry.get("raw") if "raw" in entry else hub.get("raw"),
        "stale": bool(stale),
        "updated_at": entry.get("updated_at") or entry.get("hub_updated_at"),
        "host": entry.get("host"),
        "error": entry.get("error"),
    }


def empty_device_entry(dev: dict[str, Any]) -> dict[str, Any]:
    return {
        "udid": dev.get("udid"),
        "host": dev.get("host"),
        "name": dev.get("name"),
        "product_type": dev.get("product_type"),
        "role": "device",
        "kind": classify_kind(dev.get("product_type"), dev.get("udid")),
        "battery_level": None,
        "battery_state": None,
        "stale": False,
        "updated_at": None,
        "accessories": [],
        "error": None,
    }


def mark_accessories_stale(accessories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for acc in accessories:
        acc["stale"] = True
    return accessories


def apply_legacy_aliases(entry: dict[str, Any]) -> dict[str, Any]:
    """Write hub/watch keys so older JSON readers and command_line sensors keep working."""
    view = device_battery(entry)
    accessories = entry.get("accessories") or []
    watch = first_of_kind(accessories, "watch")
    hub = {
        "udid": view.get("udid"),
        "name": view.get("name"),
        "product_type": view.get("product_type"),
        "battery_level": view.get("battery_level"),
        "battery_state": view.get("battery_state"),
    }
    if view.get("raw") is not None:
        hub["raw"] = view["raw"]
    entry["hub"] = hub if view.get("battery_level") is not None else entry.get("hub")
    entry["hub_stale"] = bool(view.get("stale"))
    entry["hub_updated_at"] = view.get("updated_at")
    if watch:
        entry["watch"] = {
            "udid": watch.get("udid"),
            "name": watch.get("name"),
            "product_type": watch.get("product_type"),
            "battery_level": watch.get("battery_level"),
            "battery_state": watch.get("battery_state"),
        }
        entry["watch_stale"] = bool(watch.get("stale"))
        entry["watch_updated_at"] = watch.get("updated_at")
    else:
        entry.pop("watch", None)
        entry["watch_stale"] = False
        entry["watch_updated_at"] = None
    return entry


def snapshot_root(devices: list[dict[str, Any]], prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Top-level phone/watch aliases for /share/idevice_battery.json."""
    prev = prev or {}
    prim = devices[0] if devices else {}
    if prim.get("udid"):
        apply_legacy_aliases(prim)
    watch = prim.get("watch") or first_of_kind(prim.get("accessories") or [], "watch")
    return {
        "phone_udid": prim.get("udid") or prev.get("phone_udid"),
        "phone": prim.get("hub") or prev.get("phone"),
        "watch": watch or prev.get("watch"),
    }
