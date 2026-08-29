#!/usr/bin/env python3
"""Ingress web UI + JSON API for iDevice Battery (port 8099)."""
from __future__ import annotations

import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from devices_store import load_store, remove_device
from pair_service import (
    finish_pair,
    get_job,
    list_usb_devices,
    start_pair_async,
    verify_device,
)

WWW = Path(os.environ.get("IDEVICE_WWW", "/www"))
BATTERY_JSON = Path(os.environ.get("IDEVICE_BATTERY_JSON", "/share/idevice_battery.json"))
HOST = os.environ.get("IDEVICE_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("IDEVICE_UI_PORT", "8099"))


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
                job = start_pair_async(body.get("udid"))
                self._json(200, job)
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
            if api.startswith("/api/devices/") and api.endswith("/remove"):
                # POST /api/devices/<udid>/remove
                udid = api[len("/api/devices/") : -len("/remove")]
                self._json(200, remove_device(udid))
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
                self._json(200, remove_device(udid))
                return
            self._json(404, {"error": "not_found"})
        except Exception as e:
            self._json(400, {"error": str(e)})


def main() -> None:
    WWW.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[ui] listening on http://{HOST}:{PORT} www={WWW}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
