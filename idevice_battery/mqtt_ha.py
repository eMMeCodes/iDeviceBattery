#!/usr/bin/env python3
"""Home Assistant MQTT discovery for iDevice Battery.

Stable identity = UDID. Display name = current DeviceName from poll.
Default suggested_area = iDevice (user can move devices later).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

PRODUCT_MAP: dict[str, str] = {}


def _load_product_map() -> dict[str, str]:
    global PRODUCT_MAP
    if PRODUCT_MAP:
        return PRODUCT_MAP
    candidates = [
        Path(__file__).resolve().parent / "product_map.json",
        Path("/product_map.json"),
    ]
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    PRODUCT_MAP = {str(k): str(v) for k, v in data.items()}
                    print(f"[mqtt] product_map loaded ({len(PRODUCT_MAP)} entries) from {path}", flush=True)
                    return PRODUCT_MAP
        except Exception as e:
            print(f"[mqtt] product_map read {path}: {e}", flush=True)
    print("[mqtt] product_map missing — raw ProductType codes will be shown", flush=True)
    PRODUCT_MAP = {}
    return PRODUCT_MAP


def model_label(product_type: str | None, fallback: str = "iDevice") -> str:
    m = _load_product_map()
    if product_type and product_type in m:
        return m[product_type]
    return product_type or fallback


DISCOVERY_PREFIX = os.environ.get("IDEVICE_MQTT_DISCOVERY_PREFIX", "homeassistant")
TOPIC_ROOT = os.environ.get("IDEVICE_MQTT_TOPIC_ROOT", "idevice")
MANUFACTURER = "iDevice Battery"
SUGGESTED_AREA = os.environ.get("IDEVICE_MQTT_AREA", "iDevice")


def udid_key(udid: str) -> str:
    """Stable short key for topics/entity_id (last UDID segment)."""
    u = (udid or "").strip()
    if "-" in u:
        u = u.rsplit("-", 1)[-1]
    return re.sub(r"[^a-zA-Z0-9]", "", u.lower()) or "unknown"


def device_ident(udid: str) -> str:
    return f"idevice_{udid_key(udid)}"


def _mqtt_env() -> dict[str, Any]:
    """Resolve broker settings from env (filled by run.sh / Supervisor)."""
    host = (os.environ.get("IDEVICE_MQTT_HOST") or "").strip()
    port = int(os.environ.get("IDEVICE_MQTT_PORT") or "1883")
    user = (os.environ.get("IDEVICE_MQTT_USER") or "").strip() or None
    password = os.environ.get("IDEVICE_MQTT_PASSWORD")
    if password == "":
        password = None
    enabled = os.environ.get("IDEVICE_MQTT_ENABLED", "1").strip() not in ("0", "false", "no")
    return {
        "enabled": enabled and bool(host),
        "host": host,
        "port": port,
        "user": user,
        "password": password,
    }


def fetch_supervisor_mqtt() -> dict[str, Any] | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        req = urllib.request.Request(
            "http://supervisor/services/mqtt",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data") if isinstance(data, dict) else None
    except Exception as e:
        print(f"[mqtt] supervisor services/mqtt failed: {e}", flush=True)
        return None


def ensure_mqtt_env_from_supervisor() -> None:
    """Fill IDEVICE_MQTT_* from options file, share file, or Supervisor."""
    if os.environ.get("IDEVICE_MQTT_USER") and os.environ.get("IDEVICE_MQTT_HOST"):
        return
    # /share/idevice_mqtt.json — fallback when Supervisor token unavailable
    share_cfg = Path("/share/idevice_mqtt.json")
    if share_cfg.is_file():
        try:
            data = json.loads(share_cfg.read_text())
            if data.get("host"):
                os.environ.setdefault("IDEVICE_MQTT_HOST", str(data["host"]))
            if data.get("port"):
                os.environ.setdefault("IDEVICE_MQTT_PORT", str(data["port"]))
            if data.get("username") or data.get("user"):
                os.environ.setdefault(
                    "IDEVICE_MQTT_USER",
                    str(data.get("username") or data.get("user")),
                )
            if data.get("password") is not None:
                os.environ.setdefault("IDEVICE_MQTT_PASSWORD", str(data["password"]))
        except Exception as e:
            print(f"[mqtt] read /share/idevice_mqtt.json failed: {e}", flush=True)
    if os.environ.get("IDEVICE_MQTT_HOST") and os.environ.get("IDEVICE_MQTT_USER"):
        return
    info = fetch_supervisor_mqtt()
    if not info:
        os.environ.setdefault("IDEVICE_MQTT_HOST", "127.0.0.1")
        return
    os.environ.setdefault("IDEVICE_MQTT_HOST", "127.0.0.1")
    if info.get("port"):
        os.environ.setdefault("IDEVICE_MQTT_PORT", str(info["port"]))
    if info.get("username"):
        os.environ.setdefault("IDEVICE_MQTT_USER", str(info["username"]))
    if info.get("password") is not None:
        os.environ.setdefault("IDEVICE_MQTT_PASSWORD", str(info["password"]))


def _client():
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:
        raise RuntimeError("paho-mqtt not installed") from e
    cfg = _mqtt_env()
    if not cfg["enabled"]:
        return None, cfg
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="idevice_battery"
        )
    except AttributeError:
        client = mqtt.Client(client_id="idevice_battery")
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"])
    client.connect(cfg["host"], cfg["port"], keepalive=30)
    client.loop_start()
    return client, cfg


def _publish(client, topic: str, payload: str, retain: bool = True) -> None:
    info = client.publish(topic, payload, qos=0, retain=retain)
    try:
        info.wait_for_publish(timeout=5)
    except Exception:
        pass


def _disconnect(client) -> None:
    try:
        client.loop_stop()
    except Exception:
        pass
    try:
        client.disconnect()
    except Exception:
        pass


def _hub_device_block(udid: str, name: str, product_type: str) -> dict[str, Any]:
    return {
        "identifiers": [device_ident(udid)],
        "name": name or model_label(product_type),
        "model": model_label(product_type),
        "manufacturer": MANUFACTURER,
        "suggested_area": SUGGESTED_AREA,
    }


def _acc_device_block(
    udid: str,
    name: str,
    product_type: str,
    via_hub_udid: str,
) -> dict[str, Any]:
    block = _hub_device_block(udid, name, product_type)
    block["via_device"] = device_ident(via_hub_udid)
    return block


def _discovery_topics(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{object_id}/config"


def publish_node(
    client,
    *,
    udid: str,
    name: str,
    product_type: str,
    battery_level: Any,
    battery_state: Any,
    via_hub_udid: str | None = None,
) -> None:
    key = udid_key(udid)
    state_batt = f"{TOPIC_ROOT}/{key}/battery"
    state_chg = f"{TOPIC_ROOT}/{key}/battery_state"
    attr_topic = f"{TOPIC_ROOT}/{key}/attributes"

    if via_hub_udid:
        device = _acc_device_block(udid, name, product_type, via_hub_udid)
        display = name or model_label(product_type, "Accessory")
    else:
        device = _hub_device_block(udid, name, product_type)
        display = name or model_label(product_type)

    batt_cfg = {
        "name": "Battery",
        "unique_id": f"idevice_{key}_battery",
        "object_id": f"idevice_{key}_battery",
        "state_topic": state_batt,
        "json_attributes_topic": attr_topic,
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "device": device,
    }
    state_cfg = {
        "name": "Battery state",
        "unique_id": f"idevice_{key}_battery_state",
        "object_id": f"idevice_{key}_battery_state",
        "state_topic": state_chg,
        "json_attributes_topic": attr_topic,
        "icon": "mdi:battery-charging",
        "device": device,
    }

    _publish(client, _discovery_topics("sensor", f"idevice_{key}_battery"), json.dumps(batt_cfg))
    _publish(
        client,
        _discovery_topics("sensor", f"idevice_{key}_battery_state"),
        json.dumps(state_cfg),
    )

    if battery_level is not None:
        _publish(client, state_batt, str(int(battery_level)))
    if battery_state is not None:
        _publish(client, state_chg, str(battery_state))
    _publish(
        client,
        attr_topic,
        json.dumps(
            {
                "udid": udid,
                "name": display,
                "product_type": product_type or None,
                "model": model_label(product_type),
                "via_hub_udid": via_hub_udid,
            }
        ),
    )
    print(f"[mqtt] published {display} ({key[:8]}…)", flush=True)


def unpublish_node(client, udid: str) -> None:
    key = udid_key(udid)
    for suffix in ("battery", "battery_state"):
        _publish(client, _discovery_topics("sensor", f"idevice_{key}_{suffix}"), "")
    for topic in (
        f"{TOPIC_ROOT}/{key}/battery",
        f"{TOPIC_ROOT}/{key}/battery_state",
        f"{TOPIC_ROOT}/{key}/attributes",
    ):
        _publish(client, topic, "")
    print(f"[mqtt] unpublished {key[:8]}…", flush=True)


def collect_udids_from_entry(entry: dict[str, Any]) -> list[str]:
    udids: list[str] = []
    hub_udid = entry.get("udid")
    if hub_udid:
        udids.append(hub_udid)
    watch = entry.get("watch") or {}
    if watch.get("udid"):
        udids.append(watch["udid"])
    for a in entry.get("accessories") or []:
        if a.get("udid"):
            udids.append(a["udid"])
    return udids


def sync_entry(entry: dict[str, Any]) -> None:
    """Publish discovery + state for one hub entry (and accessories)."""
    ensure_mqtt_env_from_supervisor()
    client, cfg = _client()
    if client is None:
        print(f"[mqtt] skip sync (enabled={cfg.get('enabled')} host={cfg.get('host')!r})", flush=True)
        return
    try:
        hub_udid = entry.get("udid")
        if not hub_udid:
            return
        hub = entry.get("hub") or {}
        name = hub.get("name") or entry.get("name") or hub_udid[:8]
        product_type = hub.get("product_type") or entry.get("product_type") or ""
        if hub.get("battery_level") is not None:
            publish_node(
                client,
                udid=hub_udid,
                name=name,
                product_type=product_type,
                battery_level=hub.get("battery_level"),
                battery_state=hub.get("battery_state") or "unknown",
            )
        else:
            # Still announce device so entity exists; state unknown
            publish_node(
                client,
                udid=hub_udid,
                name=name,
                product_type=product_type,
                battery_level=None,
                battery_state=None,
            )

        watch = entry.get("watch")
        if watch and watch.get("udid") and watch.get("battery_level") is not None:
            publish_node(
                client,
                udid=watch["udid"],
                name=watch.get("name") or "Watch",
                product_type=watch.get("product_type") or "",
                battery_level=watch.get("battery_level"),
                battery_state=watch.get("battery_state") or "unknown",
                via_hub_udid=hub_udid,
            )

        for a in entry.get("accessories") or []:
            if not a.get("udid") or a.get("battery_level") is None:
                continue
            publish_node(
                client,
                udid=a["udid"],
                name=a.get("name") or model_label(a.get("product_type"), "Accessory"),
                product_type=a.get("product_type") or "",
                battery_level=a.get("battery_level"),
                battery_state=a.get("battery_state") or "unknown",
                via_hub_udid=hub_udid,
            )
    finally:
        _disconnect(client)


def sync_battery_doc(doc: dict[str, Any]) -> None:
    for entry in doc.get("devices") or []:
        try:
            sync_entry(entry)
        except Exception as e:
            print(f"[mqtt] sync_entry failed: {e}", flush=True)


def unpublish_entry(entry: dict[str, Any] | None, udid: str) -> None:
    ensure_mqtt_env_from_supervisor()
    client, cfg = _client()
    if client is None:
        print(f"[mqtt] skip unpublish (host={cfg.get('host')!r})", flush=True)
        return
    try:
        udids = collect_udids_from_entry(entry) if entry else [udid]
        if udid not in udids:
            udids.insert(0, udid)
        for u in udids:
            unpublish_node(client, u)
    finally:
        _disconnect(client)


def entity_ids_for_udid(udid: str) -> dict[str, str]:
    key = udid_key(udid)
    return {
        "battery": f"sensor.idevice_{key}_battery",
        "battery_state": f"sensor.idevice_{key}_battery_state",
    }


def _ha_api_get(path: str) -> Any:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("no SUPERVISOR_TOKEN (homeassistant_api required)")
    req = urllib.request.Request(
        f"http://supervisor/core/api{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode())


def _lookup_from_states(udid: str, states: list) -> dict[str, Optional[str]]:
    want = (udid or "").strip()
    out: dict[str, Optional[str]] = {"battery": None, "battery_state": None}
    for st in states:
        eid = st.get("entity_id") or ""
        if not eid.startswith("sensor."):
            continue
        attrs = st.get("attributes") or {}
        if str(attrs.get("udid") or "") != want:
            continue
        if eid.endswith("_battery_state"):
            out["battery_state"] = eid
        elif eid.endswith("_battery"):
            out["battery"] = eid
    return out


def _lookup_from_entity_registry(udid: str) -> dict[str, Optional[str]]:
    """Read real entity_id by unique_id from HA entity registry on disk / share index."""
    key = udid_key(udid)
    want = {
        f"idevice_{key}_battery": "battery",
        f"idevice_{key}_battery_state": "battery_state",
    }
    out: dict[str, Optional[str]] = {"battery": None, "battery_state": None}

    # Fast path: share index maintained by add-on / host
    share_idx = Path("/share/idevice_entity_index.json")
    if share_idx.is_file():
        try:
            items = json.loads(share_idx.read_text())
            if isinstance(items, list):
                for ent in items:
                    slot = want.get(ent.get("unique_id") or "")
                    if slot:
                        out[slot] = ent.get("entity_id")
                if out.get("battery") and out.get("battery_state"):
                    return out
        except Exception as e:
            print(f"[mqtt] share entity index failed: {e}", flush=True)

    candidates = [
        Path("/homeassistant/.storage/core.entity_registry"),
        Path("/config/.storage/core.entity_registry"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
            entities = (data.get("data") or {}).get("entities") or []
            for ent in entities:
                uid = ent.get("unique_id") or ""
                slot = want.get(uid)
                if slot:
                    out[slot] = ent.get("entity_id")
            return out
        except Exception as e:
            print(f"[mqtt] registry read {path} failed: {e}", flush=True)
    return out


def update_share_entity_index(rows: list[dict[str, Any]]) -> None:
    """Merge resolved entity rows into /share/idevice_entity_index.json."""
    path = Path("/share/idevice_entity_index.json")
    by_uid: dict[str, str] = {}
    try:
        if path.is_file():
            prev = json.loads(path.read_text())
            if isinstance(prev, list):
                for ent in prev:
                    if ent.get("unique_id") and ent.get("entity_id"):
                        by_uid[ent["unique_id"]] = ent["entity_id"]
    except Exception:
        pass
    for row in rows or []:
        if row.get("unique_id_battery") and row.get("battery"):
            by_uid[row["unique_id_battery"]] = row["battery"]
        if row.get("unique_id_battery_state") and row.get("battery_state"):
            by_uid[row["unique_id_battery_state"]] = row["battery_state"]
    items = [{"unique_id": k, "entity_id": v} for k, v in sorted(by_uid.items())]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2))
        tmp.replace(path)
    except Exception as e:
        print(f"[mqtt] write entity index failed: {e}", flush=True)

def lookup_ha_entities_many(
    udids: list[str], *, retries: int = 10, delay: float = 0.4
) -> dict[str, dict[str, Optional[str]]]:
    """Resolve real entity_ids for many UDIDs in one HA poll loop."""
    import time

    want = [u for u in udids if u]
    result: dict[str, dict[str, Optional[str]]] = {
        u: {"battery": None, "battery_state": None} for u in want
    }
    if not want:
        return result

    for attempt in range(retries):
        states = None
        try:
            raw = _ha_api_get("/states")
            if isinstance(raw, list):
                states = raw
        except Exception as e:
            if attempt == 0:
                print(f"[mqtt] HA states lookup: {e}", flush=True)

        done = True
        for u in want:
            if states is not None:
                found = _lookup_from_states(u, states)
                if found.get("battery"):
                    result[u]["battery"] = found["battery"]
                if found.get("battery_state"):
                    result[u]["battery_state"] = found["battery_state"]
            if not (result[u].get("battery") and result[u].get("battery_state")):
                reg = _lookup_from_entity_registry(u)
                if reg.get("battery"):
                    result[u]["battery"] = result[u].get("battery") or reg.get("battery")
                if reg.get("battery_state"):
                    result[u]["battery_state"] = result[u].get("battery_state") or reg.get(
                        "battery_state"
                    )
            if not (result[u].get("battery") and result[u].get("battery_state")):
                done = False
        if done:
            return result
        time.sleep(delay)
    return result


def lookup_ha_entities(udid: str, *, retries: int = 10, delay: float = 0.4) -> dict[str, Optional[str]]:
    return lookup_ha_entities_many([udid], retries=retries, delay=delay).get(
        udid, {"battery": None, "battery_state": None}
    )


def resolve_entities_for_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """List Device + accessories with real HA entity_ids after MQTT discovery."""
    hub = entry.get("hub") or {}
    hub_udid = entry.get("udid") or ""
    hub_name = hub.get("name") or entry.get("name") or hub_udid[:8]
    hub_type = hub.get("product_type") or entry.get("product_type") or ""

    extras: list[dict[str, Any]] = []
    watch = entry.get("watch")
    if watch and watch.get("udid") and watch.get("battery_level") is not None:
        extras.append(watch)
    for a in entry.get("accessories") or []:
        if a and a.get("udid") and a.get("battery_level") is not None:
            extras.append(a)

    udids = [hub_udid] + [a["udid"] for a in extras if a.get("udid")]
    by_udid = lookup_ha_entities_many(udids)

    rows: list[dict[str, Any]] = []
    ids = by_udid.get(hub_udid) or {}
    rows.append(
        {
            "kind": "device",
            "udid": hub_udid,
            "name": hub_name,
            "product_type": hub_type,
            "title": f"{hub_name} - {model_label(hub_type)}"
            if hub_type and model_label(hub_type) != hub_name
            else hub_name,
            "unique_id_battery": f"idevice_{udid_key(hub_udid)}_battery" if hub_udid else None,
            "unique_id_battery_state": f"idevice_{udid_key(hub_udid)}_battery_state"
            if hub_udid
            else None,
            "battery": ids.get("battery"),
            "battery_state": ids.get("battery_state"),
        }
    )
    for a in extras:
        ids = by_udid.get(a["udid"]) or {}
        aname = a.get("name") or model_label(a.get("product_type"), "Accessory")
        atype = a.get("product_type") or ""
        auk = udid_key(a["udid"])
        rows.append(
            {
                "kind": "accessory",
                "udid": a["udid"],
                "name": aname,
                "product_type": atype,
                "title": f"{aname} - {model_label(atype)}"
                if atype and model_label(atype) != aname
                else aname,
                "unique_id_battery": f"idevice_{auk}_battery",
                "unique_id_battery_state": f"idevice_{auk}_battery_state",
                "battery": ids.get("battery"),
                "battery_state": ids.get("battery_state"),
            }
        )
    try:
        from mqtt_ha import update_share_entity_index

        update_share_entity_index(rows)
    except Exception:
        pass
    return rows

