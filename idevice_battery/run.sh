#!/bin/bash
set -euo pipefail

export HOME=/data
export IDEVICE_LOCKDOWN=/var/lib/lockdown
export IDEVICE_BATTERY_JSON=/share/idevice_battery.json
export IDEVICE_CDTUNNEL_MTU="${IDEVICE_CDTUNNEL_MTU:-16000}"
export IDEVICE_DATA=/data
export IDEVICE_WWW=/www
export IDEVICE_UI_PORT=8109
export IDEVICE_UI_HOST="${IDEVICE_UI_HOST:-0.0.0.0}"
export PYTHONPATH=/

OPTS=/data/options.json
if [ -f "$OPTS" ]; then
  POLL="$(python3 - <<'PY'
import json
opts = json.load(open("/data/options.json"))
if opts.get("poll_minutes") is not None:
    m = max(1, min(10, int(opts["poll_minutes"])))
    print(m * 60)
elif opts.get("poll_seconds") is not None:
    m = max(1, min(10, round(int(opts["poll_seconds"]) / 60) or 1))
    print(m * 60)
else:
    print(180)
PY
)"
else
  POLL=180
fi
export IDEVICE_POLL_SEC="$POLL"

# MQTT from Supervisor /share — no add-on Configuration overrides
export IDEVICE_MQTT_ENABLED="${IDEVICE_MQTT_ENABLED:-1}"
export IDEVICE_MQTT_HOST="${IDEVICE_MQTT_HOST:-}"
export IDEVICE_MQTT_PORT="${IDEVICE_MQTT_PORT:-1883}"
export IDEVICE_MQTT_AREA="${IDEVICE_MQTT_AREA:-iDevice}"
if [ -z "${IDEVICE_MQTT_USER:-}" ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  MQTT_JSON="$(curl -sS -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
    http://supervisor/services/mqtt 2>/dev/null || true)"
  if [ -n "$MQTT_JSON" ]; then
    eval "$(MQTT_JSON="$MQTT_JSON" python3 - <<'PY'
import json, os, shlex
try:
    data = json.loads(os.environ["MQTT_JSON"]).get("data") or {}
except Exception:
    data = {}
if not os.environ.get("IDEVICE_MQTT_HOST"):
    print(f"export IDEVICE_MQTT_HOST={shlex.quote('127.0.0.1')}")
if data.get("port") and not os.environ.get("IDEVICE_MQTT_PORT"):
    print(f"export IDEVICE_MQTT_PORT={shlex.quote(str(data['port']))}")
if data.get("username"):
    print(f"export IDEVICE_MQTT_USER={shlex.quote(str(data['username']))}")
if data.get("password") is not None:
    print(f"export IDEVICE_MQTT_PASSWORD={shlex.quote(str(data['password']))}")
print("echo '[mqtt] credentials from Supervisor services/mqtt'")
PY
)"
  fi
fi
if [ -z "${IDEVICE_MQTT_HOST}" ]; then
  export IDEVICE_MQTT_HOST=127.0.0.1
  echo "[mqtt] default host 127.0.0.1"
fi
# Fallback credentials file (when Supervisor token / options empty)
if [ -z "${IDEVICE_MQTT_USER:-}" ] && [ -f /share/idevice_mqtt.json ]; then
  eval "$(python3 - <<'PY'
import json, shlex
data = json.load(open("/share/idevice_mqtt.json"))
if data.get("host"):
    print(f"export IDEVICE_MQTT_HOST={shlex.quote(str(data['host']))}")
if data.get("port"):
    print(f"export IDEVICE_MQTT_PORT={shlex.quote(str(data['port']))}")
user = data.get("username") or data.get("user")
if user:
    print(f"export IDEVICE_MQTT_USER={shlex.quote(str(user))}")
if data.get("password") is not None:
    print(f"export IDEVICE_MQTT_PASSWORD={shlex.quote(str(data['password']))}")
print("echo '[mqtt] credentials from /share/idevice_mqtt.json'")
PY
)"
fi
if [ -n "${IDEVICE_MQTT_USER:-}" ]; then
  echo "[mqtt] user=${IDEVICE_MQTT_USER} host=${IDEVICE_MQTT_HOST}:${IDEVICE_MQTT_PORT}"
else
  echo "[mqtt] WARNING: no mqtt_user — enable MQTT add-on / Supervisor API (mqtt:need)"
fi

echo "=== iDevice Battery $(date -Iseconds) poll=${POLL}s ($((POLL / 60)) min) ==="

mkdir -p /data/lockdown /data/.pymobiledevice3 /var/lib /run/avahi-daemon /run/dbus /var/run /share
ln -sfn /data/lockdown /var/lib/lockdown
chmod 777 /data/lockdown
if [ -d /share/idevice_lockdown_backup ] && [ -z "$(ls -A /data/lockdown 2>/dev/null)" ]; then
  cp -a /share/idevice_lockdown_backup/. /data/lockdown/ 2>/dev/null || true
  chmod -R 777 /data/lockdown 2>/dev/null || true
  echo "[migrate] lockdown restored from /share/idevice_lockdown_backup"
fi

python3 - <<'PY' || true
from rsd_battery import restore_remote_pair_records, backup_remote_pair_records
n = restore_remote_pair_records()
print(f"[migrate] remote pairing restored={n}", flush=True)
n2 = backup_remote_pair_records()
print(f"[migrate] remote pairing backed up={n2}", flush=True)
PY

# Show registry size (do not seed)
python3 - <<'PY'
from devices_store import load_store
s = load_store()
print(f"devices={len(s.get('devices') or [])}", flush=True)
PY

dbus-daemon --system --fork 2>/dev/null || true
rm -f /run/avahi-daemon/pid
avahi-daemon --daemonize --no-chroot 2>/tmp/avahi.err || true
usbmuxd 2>/dev/null || true
sleep 1

echo "=== enable wifi lockdown (best-effort, all paired devices) ==="
python3 - <<'PY' || true
from devices_store import listed_devices
from pair_service import _enable_wifi_connections

devs = listed_devices()
if not devs:
    print("no device yet — use Add in the UI")
else:
    for dev in devs:
        udid = dev.get("udid")
        if udid:
            print(f"wifi-connections on {udid[:8]}…", flush=True)
            _enable_wifi_connections(udid)
PY

echo "=== start Ingress UI :8109 ==="
python3 /webui.py &
UI_PID=$!

echo "=== poll loop ==="
IDEVICE_MQTT_CLIENT_ID=idevice_battery_poll python3 /rsd_battery.py &
POLL_PID=$!

cleanup() {
  kill "$UI_PID" "$POLL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

START_TS="$(date +%s)"
JSON="${IDEVICE_BATTERY_JSON}"
# Wait on either; if one dies, exit so s6 restarts.
# If registry has devices and JSON is not rewritten for > 2× poll, kill poller.
while kill -0 "$UI_PID" 2>/dev/null && kill -0 "$POLL_PID" 2>/dev/null; do
  sleep 5
  if python3 - "$POLL" "$START_TS" "$JSON" <<'PY'
import json, os, sys, time

poll = int(sys.argv[1])
start = int(sys.argv[2])
path = sys.argv[3]
now = time.time()
if now - start < poll + 60:
    raise SystemExit(0)
try:
    store = json.load(open("/data/devices.json"))
except Exception:
    raise SystemExit(0)
if not (store.get("devices") or []):
    raise SystemExit(0)
try:
    age = now - os.path.getmtime(path)
except OSError:
    raise SystemExit(1)
raise SystemExit(1 if age > 2 * poll else 0)
PY
  then
    :
  else
    echo "poll watchdog: ${JSON} too old — restarting add-on" >&2
    kill "$POLL_PID" 2>/dev/null || true
    exit 1
  fi
done
echo "a service exited — restarting add-on" >&2
exit 1
