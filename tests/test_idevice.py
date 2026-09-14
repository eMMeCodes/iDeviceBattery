"""Minimal CI: JSON lock, poll timeout, MQTT stale plan, Ingress, one RemotePairing host."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import devices_store as ds
import mqtt_ha
import rsd_battery as rb
from mqtt_ha import node_publish_plan
from rsd_battery import pick_remotepairing_service
from webui import client_allowed


def test_battery_json_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "DATA", tmp_path)
    monkeypatch.setattr(ds, "BATTERY_PATH", tmp_path / "batt.json")
    monkeypatch.setattr(ds, "BATTERY_LOCK_PATH", tmp_path / "batt.lock")

    def writer(n: int) -> None:
        ds.write_battery_doc({"n": n, "devices": [{"udid": f"u{n}"}]})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(40)))

    doc = ds.read_battery_doc()
    assert "n" in doc
    raw = (tmp_path / "batt.json").read_text()
    json.loads(raw)


def test_stale_doc_keeps_last_percent():
    prev = {
        "devices": [
            {
                "udid": "PHONE",
                "battery_level": 90,
                "stale": False,
                "accessories": [
                    {"udid": "WATCH", "battery_level": 81, "stale": False}
                ],
            }
        ]
    }
    doc = rb._stale_doc(prev, "poll timed out after 55s")
    phone = doc["devices"][0]
    assert phone["battery_level"] == 90
    assert phone["stale"] is True
    assert phone["accessories"][0]["stale"] is True
    assert "timed out" in doc["error"]


def test_poll_cycle_timeout_does_not_hang(monkeypatch):
    async def hang():
        await asyncio.sleep(30)
        return {"devices": []}

    monkeypatch.setattr(rb, "FETCH_ONCE_TIMEOUT", 0.05)
    monkeypatch.setattr(rb, "fetch_once", hang)
    monkeypatch.setattr(
        rb,
        "_load_prev",
        lambda: {
            "devices": [
                {"udid": "PHONE", "battery_level": 41, "stale": False, "accessories": []}
            ]
        },
    )
    doc = asyncio.run(rb.poll_cycle())
    assert "timed out" in (doc.get("error") or "")
    assert doc["devices"][0]["battery_level"] == 41
    assert doc["devices"][0]["stale"] is True


def test_mqtt_stale_is_unavailable_not_fake_live():
    plan = node_publish_plan(
        udid="00008030-000000000000001E",
        name="Test Phone",
        product_type="iPhone16,1",
        battery_level=90,
        battery_state="Not Charging",
        stale=True,
        last_updated="2026-09-13T21:59:00+00:00",
        kind="iphone",
        role="device",
    )
    assert plan["available"] is False
    assert plan["availability"] == "offline"
    assert plan["publish_battery"] is False
    assert plan["attributes"]["stale"] is True
    assert plan["last_updated"] == "2026-09-13T21:59:00+00:00"


def test_mqtt_fresh_publishes_percent():
    plan = node_publish_plan(
        udid="PHONE",
        name="Test Phone",
        product_type="iPhone16,1",
        battery_level=85,
        battery_state="charging",
        stale=False,
        last_updated="2026-09-14T06:00:00+00:00",
    )
    assert plan["available"] is True
    assert plan["availability"] == "online"
    assert plan["publish_battery"] is True
    assert plan["battery_level"] == 85


def test_expire_after_covers_two_missed_polls():
    assert mqtt_ha.expire_after_seconds(180) >= 540


def test_ingress_allowlist():
    assert client_allowed("172.30.32.2", allow_lan=False)
    assert client_allowed("127.0.0.1", allow_lan=False)
    assert client_allowed("::1", allow_lan=False)
    assert client_allowed("::ffff:172.30.32.2", allow_lan=False)
    assert not client_allowed("192.168.1.50", allow_lan=False)
    assert client_allowed("192.168.1.50", allow_lan=True)


def test_pick_one_remotepairing_host():
    dup = SimpleNamespace(hostname="10.0.0.8", port=49152)
    want = SimpleNamespace(hostname="192.168.1.20", port=49152)
    services = [dup, dup, want] * 12
    assert len(services) == 36
    picked = pick_remotepairing_service(services, prefer_host="192.168.1.20")
    assert picked is want


def test_supervisor_mqtt_uses_service_host(monkeypatch):
    monkeypatch.delenv("IDEVICE_MQTT_HOST", raising=False)
    monkeypatch.delenv("IDEVICE_MQTT_USER", raising=False)
    monkeypatch.delenv("IDEVICE_MQTT_PORT", raising=False)
    monkeypatch.delenv("IDEVICE_MQTT_PASSWORD", raising=False)
    monkeypatch.setattr(
        mqtt_ha,
        "fetch_supervisor_mqtt",
        lambda: {
            "host": "core-mosquitto",
            "port": 1883,
            "username": "ha",
            "password": "secret",
        },
    )
    orig = mqtt_ha.Path.is_file

    def no_share(self):
        if str(self).endswith("idevice_mqtt.json"):
            return False
        return orig(self)

    monkeypatch.setattr(mqtt_ha.Path, "is_file", no_share)
    mqtt_ha.ensure_mqtt_env_from_supervisor()
    assert mqtt_ha.os.environ.get("IDEVICE_MQTT_HOST") == "core-mosquitto"
    assert mqtt_ha.os.environ.get("IDEVICE_MQTT_USER") == "ha"
