# Pairing guide

You need **two** pairings. Both are done **once** over USB (unless you reset
Trust or wipe the phone).

## 1. Prerequisites

- iPhone unlocked, on the same network as Home Assistant
- Prefer a **static DHCP lease** for `phone_host`
- Developer Mode is **not** required for the RemotePairing path used here
  (verified with DeveloperModeStatus off)

## 2. Find the UDID

With the phone on USB and the add-on (or a pymobiledevice3 shell) able to see it:

```bash
idevice_id -l
# or
pymobiledevice3 usbmux list
```

Put that value in add-on option `phone_udid`.

## 3. Lockdown Trust

```bash
pymobiledevice3 lockdown pair
```

Accept **Trust** on the iPhone. Record lands in:

```text
/data/lockdown/<UDID>.plist
```

(symlink target used by the add-on: `/var/lib/lockdown` → `/data/lockdown`)

## 4. RemotePairing (required for Watch over Wi-Fi)

```bash
pymobiledevice3 lockdown remotepairing --pair
```

Record lands in:

```text
/data/.pymobiledevice3/remote_<UDID>.plist
```

Without this file, Watch polls fail with:

```text
no RemotePairing on Bonjour; USB once: pymobiledevice3 lockdown remotepairing --pair
```

## 5. Enable Wi-Fi lockdown (optional, best-effort)

On start, `run.sh` tries to set:

- `EnableWifiConnections`
- `EnableWifiDebugging`

via lockdown on `phone_host:62078`. If the phone is asleep, this step is skipped;
USB pairing is still enough for later Wi-Fi sessions when the phone wakes.

## 6. Unplug and verify

1. Start the add-on
2. Unlock the iPhone (screen on / Wi-Fi associated)
3. Check `/share/idevice_battery.json` for non-null `phone` and `watch`
4. Add-on logs should show `PHONE_OK`, `REMOTEPAIRING`, `TUNNEL_OK`, `WATCH_OK`

## Re-pairing

Re-run the USB steps if:

- You tapped **Don't Trust** / reset Location & Privacy
- You erased the phone
- You deleted the add-on data volume
- RemotePairing record is missing after an add-on reinstall without restoring `/data`
