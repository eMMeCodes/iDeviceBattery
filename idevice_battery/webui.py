#!/usr/bin/env python3
"""Ingress web UI + JSON API for iDevice Battery (Ingress port 8109)."""
from __future__ import annotations

import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from devices_store import load_store, remove_device
from pair_service import (
    finish_pair,
    get_job,
    list_usb_devices,
    start_pair_async,
    verify_device,
)

def _resolve_www() -> Path:
    overlay = Path("/share/idevice_ui")
    env_on = os.environ.get("IDEVICE_UI_OVERLAY", "").strip() in ("1", "true", "yes")
    flag = (overlay / ".enable").is_file()
    if (env_on or flag) and (overlay / "app.js").is_file():
        return overlay
    return Path(os.environ.get("IDEVICE_WWW", "/www"))


WWW = _resolve_www()
BATTERY_JSON = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
HOST = os.environ.get("IDEVICE_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("IDEVICE_UI_PORT", "8109"))


def _force_check(udid: str) -> dict[str, Any]:
    """Run one poll for a paired device and merge into battery JSON."""
    from datetime import datetime, timezone

    store = load_store()
    dev = next((d for d in store.get("devices") or [] if d.get("udid") == udid), None)
    if not dev:
        raise RuntimeError("device not found")
    result = verify_device(dev["udid"], dev["host"])

    prev: dict[str, Any] = {}
    try:
        if BATTERY_JSON.exists():
            prev = json.loads(BATTERY_JSON.read_text())
    except Exception:
        prev = {}

    prev_entry = next(
        (e for e in (prev.get("devices") or []) if e.get("udid") == udid),
        {},
    )
    hub = result.get("hub") or prev_entry.get("hub")
    watch = result.get("watch") or prev_entry.get("watch")
    accessories = result.get("accessories")
    if accessories is None:
        accessories = prev_entry.get("accessories") or []
    hub_ok = hub and hub.get("battery_level") is not None
    entry = {
        "udid": dev["udid"],
        "host": dev["host"],
        "name": (hub or {}).get("name") or dev.get("name"),
        "product_type": (hub or {}).get("product_type") or dev.get("product_type"),
        "hub": hub,
        "watch": watch,
        "accessories": accessories,
        "error": result.get("error") if not hub_ok else None,
        "accessory_note": result.get("accessory_note"),
    }

    ordered = []
    for d in store.get("devices") or []:
        if d.get("udid") == udid:
            ordered.append(entry)
        else:
            old = next(
                (e for e in (prev.get("devices") or []) if e.get("udid") == d.get("udid")),
                None,
            )
            ordered.append(
                old
                or {
                    "udid": d["udid"],
                    "host": d["host"],
                    "name": d.get("name"),
                    "product_type": d.get("product_type"),
                    "hub": None,
                    "watch": None,
                    "accessories": [],
                    "error": None,
                }
            )

    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": "remotepairing-userspace-rsd",
        "devices": ordered,
        "phone_udid": ordered[0]["udid"] if ordered else udid,
        "phone": ordered[0].get("hub") if ordered else hub,
        "watch": ordered[0].get("watch") if ordered else watch,
        "error": entry.get("error") if not hub_ok else None,
    }
    prim = (store.get("devices") or [{}])[0]
    if prim.get("udid") == udid:
        doc["phone_udid"] = udid
        if hub:
            doc["phone"] = hub
        if watch:
            doc["watch"] = watch

    BATTERY_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = BATTERY_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(BATTERY_JSON)
    try:
        from mqtt_ha import sync_entry

        sync_entry(entry)
    except Exception as e:
        print(f"[mqtt] force_check sync failed: {e}", flush=True)
    return {"result": result, "battery": doc}


def _force_discover(udid: str) -> dict[str, Any]:
    """Re-scan companion registry for Watch / AirPods / other accessories."""
    import asyncio
    from datetime import datetime, timezone

    import rsd_battery as rb

    store = load_store()
    dev = next((d for d in store.get("devices") or [] if d.get("udid") == udid), None)
    if not dev:
        raise RuntimeError("device not found")

    prev: dict[str, Any] = {}
    try:
        if BATTERY_JSON.exists():
            prev = json.loads(BATTERY_JSON.read_text())
    except Exception:
        prev = {}
    prev_entry = next(
        (e for e in (prev.get("devices") or []) if e.get("udid") == udid),
        {},
    )

    comp = asyncio.run(
        rb._companion_via_remotepairing(host=dev["host"], udid=dev["udid"])
    )
    if comp.get("watch") or comp.get("accessories"):
        watch = comp.get("watch") or prev_entry.get("watch")
        accessories = comp.get("accessories") or []
    else:
        watch = prev_entry.get("watch")
        accessories = prev_entry.get("accessories") or []
    hub = prev_entry.get("hub")

    entry = {
        "udid": dev["udid"],
        "host": dev["host"],
        "name": prev_entry.get("name") or dev.get("name"),
        "product_type": prev_entry.get("product_type") or dev.get("product_type"),
        "hub": hub,
        "watch": watch,
        "accessories": accessories,
        "error": prev_entry.get("error"),
        "accessory_note": comp.get("error"),
    }

    ordered = []
    for d in store.get("devices") or []:
        if d.get("udid") == udid:
            ordered.append(entry)
        else:
            old = next(
                (e for e in (prev.get("devices") or []) if e.get("udid") == d.get("udid")),
                None,
            )
            ordered.append(old or {"udid": d["udid"], "host": d["host"], "name": d.get("name")})

    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": "remotepairing-userspace-rsd",
        "devices": ordered,
        "phone_udid": prev.get("phone_udid"),
        "phone": prev.get("phone"),
        "watch": prev.get("watch"),
        "error": prev.get("error"),
    }
    prim = (store.get("devices") or [{}])[0]
    if prim.get("udid") == udid:
        doc["phone_udid"] = udid
        if hub:
            doc["phone"] = hub
        if watch:
            doc["watch"] = watch

    BATTERY_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = BATTERY_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(BATTERY_JSON)
    try:
        from mqtt_ha import sync_entry

        sync_entry(entry)
    except Exception as e:
        print(f"[mqtt] force_discover sync failed: {e}", flush=True)
    return {"companion": comp, "battery": doc}


def _remove_paired_device(udid: str) -> dict[str, Any]:
    """Remove from registry and clear MQTT discovery for hub + known accessories."""
    prev_entry = None
    try:
        batt = _read_battery()
        prev_entry = next(
            (e for e in (batt.get("devices") or []) if e.get("udid") == udid),
            None,
        )
    except Exception:
        prev_entry = None
    store = remove_device(udid)
    try:
        from mqtt_ha import unpublish_entry

        unpublish_entry(prev_entry, udid)
    except Exception as e:
        print(f"[mqtt] unpublish failed: {e}", flush=True)
    return store


def _read_battery() -> dict[str, Any]:
    try:
        if BATTERY_JSON.exists():
            return json.loads(BATTERY_JSON.read_text())
    except Exception:
        pass
    return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "iDeviceBatteryUI/0.6"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} {fmt % args}", flush=True)

    def _cors(self) -> None:
        self.send_header("Cache-Control", "no-store")

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode() or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _static(self, rel: str) -> None:
        if rel in ("", "/"):
            rel = "/index.html"
        path = (WWW / rel.lstrip("/")).resolve()
        if not str(path).startswith(str(WWW.resolve())) or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        # Ingress may strip prefix; also accept nested paths
        if path.startswith("/api/"):
            api = path
        elif "/api/" in path:
            api = path[path.index("/api/") :]
        else:
            rel = path if path not in ("", "/") else "/index.html"
            # Under some proxies path may include a prefix; use basename for assets
            if rel.count("/") > 1 and not rel.startswith("/api"):
                base = rel.rsplit("/", 1)[-1]
                if base in ("", "index.html") or base.endswith((".js", ".css", ".html", ".svg", ".png")):
                    rel = "/" + (base or "index.html")
            self._static(rel)
            return

        try:
            if api == "/api/status":
                store = load_store()
                batt = _read_battery()
                self._json(
                    200,
                    {
                        "store": store,
                        "battery": batt,
                        "job": get_job(),
                    },
                )
                return
            if api == "/api/usb":
                self._json(200, {"devices": list_usb_devices()})
                return
            if api == "/api/pair/status":
                self._json(200, get_job())
                return
            self._json(404, {"error": "not_found"})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        api = path if path.startswith("/api/") else (
            path[path.index("/api/") :] if "/api/" in path else path
        )
        body = self._read_json()
        try:
            if api == "/api/pair/start":
                job = start_pair_async(
                    body.get("udid"),
                    force=bool(body.get("force")),
                )
                self._json(200, job)
                return
            if api == "/api/resolve-entities":
                from mqtt_ha import _lookup_from_entity_registry, udid_key

                rows_in = body.get("rows") or []
                out = []
                for row in rows_in:
                    r = dict(row)
                    udid = r.get("udid") or ""
                    ids = _lookup_from_entity_registry(udid) if udid else {}
                    if ids.get("battery"):
                        r["battery"] = ids["battery"]
                    if ids.get("battery_state"):
                        r["battery_state"] = ids["battery_state"]
                    # Also try unique_id keys if provided
                    if not r.get("battery") and r.get("unique_id_battery"):
                        # registry scan by unique_id already covered via udid_key
                        pass
                    if udid and not r.get("unique_id_battery"):
                        k = udid_key(udid)
                        r["unique_id_battery"] = f"idevice_{k}_battery"
                        r["unique_id_battery_state"] = f"idevice_{k}_battery_state"
                    out.append(r)
                self._json(200, {"rows": out})
                return
            if api == "/api/pair/finish":
                result = finish_pair(
                    host=str(body.get("host") or ""),
                    name=body.get("name"),
                )
                self._json(200, result)
                return
            if api == "/api/pair/verify":
                udid = str(body.get("udid") or "")
                host = str(body.get("host") or "")
                self._json(200, verify_device(udid, host))
                return
            if api == "/api/pair/wifi-host":
                from pair_service import discover_wifi_host, _enable_wifi_connections

                udid = str(body.get("udid") or "")
                if not udid:
                    raise RuntimeError("udid required")
                try:
                    _enable_wifi_connections(udid)
                except Exception:
                    pass
                host = discover_wifi_host(udid)
                self._json(200, {"host": host, "udid": udid})
                return
            if api.startswith("/api/devices/") and api.endswith("/discover"):
                udid = api[len("/api/devices/") : -len("/discover")]
                self._json(200, _force_discover(udid))
                return
            if api.startswith("/api/devices/") and api.endswith("/check"):
                udid = api[len("/api/devices/") : -len("/check")]
                self._json(200, _force_check(udid))
                return
            if api.startswith("/api/devices/") and api.endswith("/remove"):
                # POST /api/devices/<udid>/remove
                udid = api[len("/api/devices/") : -len("/remove")]
                self._json(200, _remove_paired_device(udid))
                return
            self._json(404, {"error": "not_found"})
        except Exception as e:
            traceback.print_exc()
            self._json(400, {"error": str(e)})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        api = path if path.startswith("/api/") else (
            path[path.index("/api/") :] if "/api/" in path else path
        )
        try:
            if api.startswith("/api/devices/"):
                udid = api[len("/api/devices/") :]
                self._json(200, _remove_paired_device(udid))
                return
            self._json(404, {"error": "not_found"})
        except Exception as e:
            self._json(400, {"error": str(e)})


def main() -> None:
    WWW.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[ui] listening on http://{HOST}:{PORT} www={WWW} (overlay={WWW == Path('/share/idevice_ui')})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
