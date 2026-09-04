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
    """Accessories list from a device entry (no extra top-level slots)."""
    entry = entry or {}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in entry.get("accessories") or []:
        if not isinstance(raw, dict):
            continue
        acc = normalize_accessory(raw)
        if not acc:
            continue
        key = str(acc.get("udid") or acc.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(acc)
    return out


def device_battery(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical device battery view from flat entry fields."""
    entry = entry or {}
    product = entry.get("product_type")
    return {
        "role": "device",
        "kind": entry.get("kind") or classify_kind(product, entry.get("udid")),
        "udid": entry.get("udid") or "",
        "name": entry.get("name"),
        "product_type": product,
        "battery_level": entry.get("battery_level"),
        "battery_state": entry.get("battery_state"),
        "raw": entry.get("raw"),
        "stale": bool(entry.get("stale")),
        "updated_at": entry.get("updated_at"),
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
