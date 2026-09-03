# Pairing guide

Preferred path: add-on Ingress UI → **+ Add**. The steps below are the same
operations the wizard runs (useful if you debug from a shell).

You need **two** pairings. Both are done **once** over USB (unless you reset
Trust or wipe the device).

## 1. Prerequisites

- Device unlocked, on the same network as Home Assistant
- Prefer a **static DHCP lease** for the hub IP
- Developer Mode is **not** required for the RemotePairing path used here
  (verified with DeveloperModeStatus off)

## 2. Find the UDID (CLI only)

With the device on USB:

```bash
idevice_id -l
# or
pymobiledevice3 usbmux list
```

The wizard stores UDID automatically. There is no `phone_udid` add-on option.

## 3. Lockdown Trust

```bash
pymobiledevice3 lockdown pair
```

Accept **Trust** on the iPhone. Record lands in:

```text
/data/lockdown/<UDID>.plist
```

(symlink target used by the add-on: `/var/lib/lockdown` → `/data/lockdown`)

## 4. RemotePairing (required for accessories over Wi-Fi)

```bash
pymobiledevice3 lockdown remotepairing --pair
```

Record lands in:

```text
/data/.pymobiledevice3/remote_<UDID>.plist
```

Without this file, Watch polls fail with `RemotePairing record missing`.
The **+ Add** wizard creates the record over USB (`lockdown remotepairing --pair`).

## 5. Enable Wi-Fi lockdown (optional, best-effort)

On start, `run.sh` tries to set:

- `EnableWifiConnections`
- `EnableWifiDebugging`

via lockdown on the hub IP `:62078`. If the device is asleep, this step is skipped;
USB pairing is still enough for later Wi-Fi sessions when it wakes.

## 6. Unplug and verify

1. Start the add-on and finish **+ Add** in the Ingress UI
2. Unlock the device (screen on / Wi‑Fi associated)
3. The card should show **Online** and a battery %; expand for `entity_id`s
4. Optional: `/share/idevice_battery.json` still has hub / accessory snapshots

## Re-pairing

Re-run the USB steps if:

- You tapped **Don't Trust** / reset Location & Privacy
- You erased the phone
- You deleted the add-on data volume
- RemotePairing record is missing after an add-on reinstall without restoring `/data`
