# iDevice Battery for Home Assistant

Read **iPhone** and **Apple Watch** battery level / charging state into Home Assistant
**without** relying on the Home Assistant Companion app for the Watch.

This is the Linux / HA OS equivalent of the values that
[AirBattery](https://github.com/lihaoyun6/AirBattery) shows on macOS.

| Device | How data is obtained |
|--------|----------------------|
| iPhone | Wi-Fi lockdown (`:62078`) + Trust pair record → `com.apple.mobile.battery` |
| Apple Watch | **RemotePairing** (USB once) → userspace CDTunnel → RSD → `companion_proxy` |

> Classic AirBattery path (Apple **usbmuxd** + Wi-Fi `companion_proxy`) does **not**
> work on Linux. Apple usbmuxd is not available; Debian `usbmuxd` is USB-only.
> Production path on HA OS is **RemotePairing + RSD userspace**.

## Repository layout

```
addon/                      Home Assistant local add-on (copy to /addons/idevice_pair/)
docs/                       Architecture, pairing, troubleshooting
homeassistant/
  packages/                 command_line sensors reading /share/idevice_battery.json
  lovelace/                 Bubble Card example
```

## Requirements

- Home Assistant OS / Supervised (tested on HA OS 18 + Core 2026.8, aarch64)
- iPhone on the same LAN, with **Wireless debugging / Wi-Fi sync** style lockdown
- One-time USB connection to the HA host for Trust + RemotePairing
- Add-on privileges: `usb`, `udev`, `host_network`, `share:rw`, AppArmor disabled

## Quick start

### 1. Install the add-on

```bash
cp -a addon /addons/idevice_pair
# Supervisor → Local add-ons → iDevice Battery → Install
```

Set options:

| Option | Description |
|--------|-------------|
| `phone_udid` | iPhone UDID (Settings → General → About, or `idevice_id -l` over USB) |
| `phone_host` | iPhone LAN IP (static DHCP recommended) |
| `poll_seconds` | Poll interval (default `120`) |

### 2. Pair once (USB)

Plug the iPhone into the HA machine (or a USB port visible to the add-on), Trust the computer, then inside the add-on container (or a one-shot shell with the same `/data` volume):

```bash
# Lockdown Trust pair record → /data/lockdown/<UDID>.plist
pymobiledevice3 lockdown pair

# RemotePairing record → /data/.pymobiledevice3/remote_<UDID>.plist
pymobiledevice3 lockdown remotepairing --pair
```

Unplug USB after pairing succeeds. Daily use is Wi-Fi only.

### 3. Start the add-on

Output file:

```text
/share/idevice_battery.json
```

Example:

```json
{
  "ts": "2026-08-29T20:50:44.035444+00:00",
  "phone": {
    "battery_level": 81,
    "battery_state": "charging",
    "name": "Mal9000",
    "product_type": "iPhone15,4"
  },
  "watch": {
    "udid": "00008310-…",
    "battery_level": 88,
    "battery_state": "Not Charging"
  },
  "path": "remotepairing-userspace-rsd",
  "error": null
}
```

### 4. Home Assistant sensors

Copy `homeassistant/packages/idevice_battery.yaml` into your config packages
(or merge the `command_line:` block), then check config and reload/restart.

### 5. Dashboard (optional)

See `homeassistant/lovelace/bubble_card_example.yaml` for a Bubble Card with
phone/watch chips and last-update on the secondary line.

## Charging state semantics

Apple may report `BatteryIsCharging: false` while the cable is connected
(`ExternalConnected: true`) during **Optimized Battery Charging** (~80% hold).

This add-on maps phone state as:

| Condition | `battery_state` |
|-----------|-----------------|
| `FullyCharged` | `full` |
| `BatteryIsCharging` **or** `ExternalConnected` | `charging` |
| otherwise | `Not Charging` |

Watch state still uses companion `BatteryIsCharging` only.

## Sleep / reachability

When the iPhone sleeps, Bonjour RemotePairing and lockdown `:62078` often disappear.
The poll fails; the writer **retains the last good phone/watch objects** and sets `error`.
Unlock the phone (Wi-Fi on) and wait up to one poll cycle (~`poll_seconds`).

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — why this path, what was ruled out
- [Pairing](docs/PAIRING.md) — Trust + RemotePairing details
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## License

MIT — see [LICENSE](LICENSE).

`pymobiledevice3` and Apple protocols are third-party; this project only orchestrates them for HA.
