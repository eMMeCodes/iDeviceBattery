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


def _stale_plan(**over):
    args = dict(
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
    args.update(over)
    return node_publish_plan(**args)


def test_mqtt_stale_keeps_the_last_known_percent():
    plan = _stale_plan(behavior="last_known")
    assert plan["available"] is True
    assert plan["availability"] == "online"
    assert plan["publish_battery"] is True
    assert plan["battery_level"] == 90
    # The card shows the age, not a hole.
    assert plan["attributes"]["stale"] is True
    assert plan["last_updated"] == "2026-09-13T21:59:00+00:00"


def test_mqtt_stale_can_be_hidden_on_request():
    plan = _stale_plan(behavior="unavailable")
    assert plan["available"] is False
    assert plan["availability"] == "offline"
    assert plan["publish_battery"] is False
    assert plan["attributes"]["stale"] is True


def test_mqtt_never_read_stays_unavailable():
    for behavior in ("last_known", "unavailable"):
        plan = _stale_plan(battery_level=None, stale=False, behavior=behavior)
        assert plan["available"] is False
        assert plan["publish_battery"] is False


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


def test_merge_accessories_keeps_missing_as_stale():
    from model import merge_accessories

    previous = [
        {"udid": "WATCH", "name": "Watch", "battery_level": 80, "stale": False, "product_type": "Watch6,1"},
        {"udid": "PODS", "name": "Pods", "battery_level": 50, "stale": False, "product_type": "AirPods"},
    ]
    found = [{"udid": "PODS", "name": "Pods", "battery_level": 40, "product_type": "AirPods"}]
    merged = merge_accessories(previous, found, "2026-10-26T00:00:00+00:00")
    by_udid = {row["udid"]: row for row in merged}
    assert by_udid["PODS"]["stale"] is False
    assert by_udid["PODS"]["battery_level"] == 40
    assert by_udid["WATCH"]["stale"] is True
    assert by_udid["WATCH"]["battery_level"] == 80

    empty = merge_accessories(previous, [], "2026-10-26T00:00:00+00:00")
    assert empty and all(row["stale"] for row in empty)


def test_settle_timeout_closes_later_without_cancelling():
    async def scenario():
        closed = []

        async def slow():
            await asyncio.sleep(0.15)

            def close():
                closed.append(True)

            return SimpleNamespace(close=close)

        task = asyncio.create_task(slow())
        result = await rb.settle(task, 0.05)
        assert result is None
        assert not task.cancelled()
        await asyncio.sleep(0.25)
        assert task.done()
        assert closed == [True]

    asyncio.run(scenario())


def test_settle_cancel_does_not_cancel_inner():
    async def scenario():
        finished = asyncio.Event()

        async def slow():
            await asyncio.sleep(0.15)
            finished.set()
            return SimpleNamespace(close=lambda: None)

        task = asyncio.create_task(slow())
        outer = asyncio.create_task(rb.settle(task, 2))
        await asyncio.sleep(0.02)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        assert not task.cancelled()
        await asyncio.sleep(0.25)
        assert finished.is_set()

    asyncio.run(scenario())


def test_remotepairing_endpoints_prefer_saved_host():
    from rsd_battery import remotepairing_endpoints

    addr = lambda ip: SimpleNamespace(ip=ip, full_ip=ip)
    answers = [
        SimpleNamespace(port=49152, host="other.local", addresses=[addr("10.0.0.8")]),
        SimpleNamespace(port=49153, host="phone.local", addresses=[addr("192.168.1.94")]),
    ]
    endpoints = remotepairing_endpoints(answers, prefer_host="192.168.1.94")
    assert endpoints[0] == ("192.168.1.94", 49153)


def test_remotepairing_endpoints_keep_one_ipv4_first():
    from rsd_battery import remotepairing_endpoints

    addr = lambda ip: SimpleNamespace(ip=ip, full_ip=ip)
    answers = [
        SimpleNamespace(port=49152, host="phone.local", addresses=[addr("fe80::1"), addr("192.168.1.36")]),
        SimpleNamespace(port=49152, host="phone.local", addresses=[addr("192.168.1.36")]),
        SimpleNamespace(port=49153, host="", addresses=[addr("192.168.1.94")]),
    ] * 10
    endpoints = remotepairing_endpoints(answers)
    assert endpoints[0][0] == "192.168.1.36"
    assert ("192.168.1.36", 49152) in endpoints
    assert len(endpoints) == len(set(endpoints))
    assert all(not ip.startswith("fe80:") for ip, _ in endpoints)


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
