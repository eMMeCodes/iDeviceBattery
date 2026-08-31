#!/bin/bash
set -euo pipefail

export HOME=/data
export IDEVICE_LOCKDOWN=/var/lib/lockdown
export IDEVICE_BATTERY_JSON=/share/idevice_battery.json
export IDEVICE_CDTUNNEL_MTU="${IDEVICE_CDTUNNEL_MTU:-16000}"
export IDEVICE_DATA=/data
export IDEVICE_WWW=/www
# Dev overlay: only if explicitly enabled (avoids stale /share UI hijacking Ingress)
if [ "${IDEVICE_UI_OVERLAY:-0}" = "1" ] && [ -f /share/idevice_ui/app.js ]; then
  export IDEVICE_WWW=/share/idevice_ui
elif [ -f /share/idevice_ui/.enable ] && [ -f /share/idevice_ui/app.js ]; then
  export IDEVICE_WWW=/share/idevice_ui
fi
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

# Seed devices.json from legacy options if needed
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

echo "=== enable wifi lockdown (best-effort, primary device) ==="
python3 - <<'PY' || true
import json, plistlib, socket, ssl, struct, tempfile
from pathlib import Path
from devices_store import primary_device
dev = primary_device()
if not dev:
    print("no device yet — use Add in the UI"); raise SystemExit(0)
UDID, HOST = dev["udid"], dev["host"]
PLIST = Path("/var/lib/lockdown") / f"{UDID}.plist"
if not PLIST.exists():
    print("no pair record"); raise SystemExit(0)
rec = plistlib.loads(PLIST.read_bytes())

def recvall(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c: raise ConnectionError("short")
        b += c
    return b
def send(s, m):
    body = plistlib.dumps(m, fmt=plistlib.FMT_BINARY)
    s.sendall(struct.pack(">I", len(body)) + body)
def recv(s):
    n = struct.unpack(">I", recvall(s, 4))[0]
    return plistlib.loads(recvall(s, n))
def pem(d, k):
    if b"BEGIN" in d: return d if d.endswith(b"\n") else d + b"\n"
    import base64
    return f"-----BEGIN {k}-----\n{base64.encodebytes(d).decode()}-----END {k}-----\n".encode()
try:
    s = socket.create_connection((HOST, 62078), timeout=6); s.settimeout(10)
except OSError as e:
    print("lockdown unreachable", e); raise SystemExit(0)
send(s, {"Request": "QueryType", "Label": "ha"}); recv(s)
send(s, {"Request": "StartSession", "HostID": rec["HostID"], "SystemBUID": rec["SystemBUID"], "Label": "ha"})
st = recv(s)
if st.get("EnableSessionSSL"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try: ctx.set_ciphers("ALL:!aNULL:!eNULL:@SECLEVEL=0")
    except Exception: pass
    try: ctx.options |= 0x4
    except Exception: pass
    t = Path(tempfile.mkdtemp())
    (t/"c").write_bytes(pem(rec["HostCertificate"], "CERTIFICATE"))
    (t/"k").write_bytes(pem(rec["HostPrivateKey"], "PRIVATE KEY"))
    ctx.load_cert_chain(str(t/"c"), str(t/"k")); s = ctx.wrap_socket(s, server_hostname="")
for key in ("EnableWifiConnections", "EnableWifiDebugging"):
    send(s, {"Request": "SetValue", "Domain": "com.apple.mobile.wireless_lockdown", "Key": key, "Value": True, "Label": "ha"})
    print(key, recv(s))
PY

echo "=== start Ingress UI :8109 ==="
python3 /webui.py &
UI_PID=$!

echo "=== one-shot poll (if devices exist) ==="
python3 /rsd_battery.py --once || true

echo "=== poll loop ==="
python3 /rsd_battery.py &
POLL_PID=$!

cleanup() {
  kill "$UI_PID" "$POLL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait on either; if one dies, exit so s6 restarts
while kill -0 "$UI_PID" 2>/dev/null && kill -0 "$POLL_PID" 2>/dev/null; do
  sleep 5
done
echo "a service exited — restarting add-on" >&2
exit 1
