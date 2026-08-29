#!/usr/bin/env bashio
# shellcheck shell=bash
set -euo pipefail

UDID="$(bashio::config 'phone_udid')"
HOST="$(bashio::config 'phone_host')"
POLL="$(bashio::config 'poll_seconds')"

if [ -z "$UDID" ] || [ -z "$HOST" ]; then
  bashio::log.fatal "Configure phone_udid and phone_host in the add-on options"
  exit 1
fi

export IDEVICE_UDID="$UDID"
export IDEVICE_HOST="$HOST"
export IDEVICE_POLL_SEC="$POLL"
export IDEVICE_LOCKDOWN=/var/lib/lockdown
export IDEVICE_BATTERY_JSON=/share/idevice_battery.json
export IDEVICE_CDTUNNEL_MTU="${IDEVICE_CDTUNNEL_MTU:-16000}"
export HOME=/data

bashio::log.info "iDevice battery service starting (udid=${UDID} host=${HOST} poll=${POLL}s)"

mkdir -p /data/lockdown /data/.pymobiledevice3 /var/lib /run/avahi-daemon /run/dbus /var/run /share
ln -sfn /data/lockdown /var/lib/lockdown
chmod 777 /data/lockdown

dbus-daemon --system --fork 2>/dev/null || true
rm -f /run/avahi-daemon/pid
avahi-daemon --daemonize --no-chroot 2>/tmp/avahi.err || true
usbmuxd 2>/dev/null || true
sleep 1

bashio::log.info "Enable Wi-Fi lockdown (best-effort)"
python3 - <<PY || true
import plistlib, socket, ssl, struct, tempfile
from pathlib import Path
UDID = "${UDID}"
HOST = "${HOST}"
PLIST = Path("/var/lib/lockdown") / f"{UDID}.plist"
if not PLIST.exists():
    print("no pair record"); raise SystemExit(0)
rec = plistlib.loads(PLIST.read_bytes())

def recvall(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("short")
        b += c
    return b

def send(s, m):
    body = plistlib.dumps(m, fmt=plistlib.FMT_BINARY)
    s.sendall(struct.pack(">I", len(body)) + body)

def recv(s):
    n = struct.unpack(">I", recvall(s, 4))[0]
    return plistlib.loads(recvall(s, n))

def pem(d, k):
    if b"BEGIN" in d:
        return d if d.endswith(b"\n") else d + b"\n"
    import base64
    return f"-----BEGIN {k}-----\n{base64.encodebytes(d).decode()}-----END {k}-----\n".encode()

try:
    s = socket.create_connection((HOST, 62078), timeout=6)
    s.settimeout(10)
except OSError as e:
    print("lockdown unreachable", e)
    raise SystemExit(0)

send(s, {"Request": "QueryType", "Label": "ha"})
recv(s)
send(s, {
    "Request": "StartSession",
    "HostID": rec["HostID"],
    "SystemBUID": rec["SystemBUID"],
    "Label": "ha",
})
st = recv(s)
if st.get("EnableSessionSSL"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:!aNULL:!eNULL:@SECLEVEL=0")
    except Exception:
        pass
    try:
        ctx.options |= 0x4
    except Exception:
        pass
    t = Path(tempfile.mkdtemp())
    (t / "c").write_bytes(pem(rec["HostCertificate"], "CERTIFICATE"))
    (t / "k").write_bytes(pem(rec["HostPrivateKey"], "PRIVATE KEY"))
    ctx.load_cert_chain(str(t / "c"), str(t / "k"))
    s = ctx.wrap_socket(s, server_hostname="")
for key in ("EnableWifiConnections", "EnableWifiDebugging"):
    send(s, {
        "Request": "SetValue",
        "Domain": "com.apple.mobile.wireless_lockdown",
        "Key": key,
        "Value": True,
        "Label": "ha",
    })
    print(key, recv(s))
PY

bashio::log.info "One-shot poll"
python3 /rsd_battery.py --once || true

bashio::log.info "Entering poll loop"
exec python3 /rsd_battery.py
