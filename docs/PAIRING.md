# Pairing guide

Preferred path: **Open Web UI** → **Add device**. The steps below are the same
operations the wizard runs (useful if you debug from a shell).

You need **two** pairings. Both are done **once** over USB (unless you reset
Trust or wipe the device).

## 1. Prerequisites

- Device unlocked, on the same network as Home Assistant
- Prefer a **static DHCP lease** for the device IP
- Developer Mode is **not** required for the RemotePairing path used here
  (verified with DeveloperModeStatus off)

## 2. Find the UDID (CLI only)

With the device on USB:

```bash
idevice_id -l
# or
pymobiledevice3 usbmux list
```

The wizard stores UDID automatically. There is no UDID field in app options.

## 3. Lockdown Trust

```bash
pymobiledevice3 lockdown pair
```

Accept **Trust** on the iPhone. Record lands in:

```text
/data/lockdown/<UDID>.plist
```

(symlink target used by the app: `/var/lib/lockdown` → `/data/lockdown`)

## 4. RemotePairing (required for accessories over Wi-Fi)

```bash
pymobiledevice3 lockdown remotepairing --pair
```

Record lands in:

```text
/data/.pymobiledevice3/remote_<UDID>.plist
```

Without this file, accessories are skipped (device battery still works).
The **Add device** wizard creates the record over USB (`lockdown remotepairing --pair`).

## 5. Enable Wi-Fi lockdown (optional, best-effort)

On start, the app enables Wi‑Fi lockdown (`wifi-connections on`) on the
primary device when it is reachable. If the device is asleep, this step is
skipped; USB pairing is still enough for later Wi‑Fi sessions when it wakes.

## 6. Unplug and verify

1. Start the app and finish **Add device** in the Ingress UI
2. Wake the device on Wi‑Fi (lock screen is fine; unlock is not required)
3. The card should show **Online** and a battery %; expand for `entity_id`s
4. Optional: `/share/idevice_battery.json` has device / accessory snapshots

## Re-pairing

Re-run the USB steps if:

- You tapped **Don't Trust** / reset Location & Privacy
- You erased the device
- You deleted the app data volume
- RemotePairing record is missing after an app reinstall without restoring `/data`
