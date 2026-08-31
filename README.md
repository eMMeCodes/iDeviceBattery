# iDevice Battery for Home Assistant

Pair **iPhone / iPad** over USB once, then poll battery (hub + accessories) over
Wi‑Fi and publish **MQTT discovery** sensors to Home Assistant.

No Home Assistant Companion app is required for Watch or other accessories.

| Device | How data is obtained |
|--------|----------------------|
| iPhone / iPad (hub) | Wi-Fi lockdown (`:62078`) + Trust pair record → `com.apple.mobile.battery` |
| Accessories (Watch, headphones, …) | **RemotePairing** (USB once) → userspace CDTunnel → RSD → `companion_proxy` |

Accessories appear only if the hub exposes them. A device with no accessories
is a normal, valid state.

> Classic AirBattery path (Apple **usbmuxd** + Wi-Fi `companion_proxy`) does
> **not** work on Linux. Production path on HA OS is **RemotePairing + RSD**.

## Repository layout

```
idevice_battery/            Home Assistant app (source of truth for GitHub publish)
repository.yaml             Add-on store metadata
.github/workflows/          GHCR multi-arch build
docs/                       Architecture, pairing, troubleshooting
homeassistant/
  packages/                 optional command_line fallback (MQTT discovery is primary)
  lovelace/                 Bubble Card example
```

## Requirements

- Home Assistant OS / Supervised (tested on HA OS 18 + Core 2026.8, aarch64)
- Mosquitto (or another MQTT broker) — add-on requests `mqtt:need`
- Device on the same LAN as Home Assistant
- One-time USB connection to the HA host (Trust + RemotePairing)
- Add-on privileges: `usb`, `udev`, `host_network`, `share:rw`, custom AppArmor profile

## Quick start

### 1. Install the add-on

**From GitHub:** add `https://github.com/eMMeCodes/iDeviceBattery` under
**Settings → Add-ons → Add-on store → ⋮ → Repositories**, then install
**iDevice Battery**.

**Local development on HA OS:** copy the app folder and build locally (remove
`image:` from `config.yaml` first):

```bash
cp -a idevice_battery /addons/idevice_battery
# edit /addons/idevice_battery/config.yaml — delete the image: line
```

Then: **Settings → Add-ons → Add-on store → ⋮ → Check for updates → iDevice Battery → Install**.

Configuration has a single option:

| Option | Description |
|--------|-------------|
| `poll_minutes` | How often to poll (menu **1–10**, default **3**) |

MQTT host/credentials come from Supervisor. Optional fallback file:
`/share/idevice_mqtt.json` (`host`, `port`, `username`, `password`).

### 2. Pair devices (Ingress UI)

Open the add-on **Open Web UI** (or sidebar **iDevice Battery**) and tap **+ Add**.

The wizard:

1. Unlock the device and plug it into the HA machine via USB
2. Tap **Trust** on the device if asked (re-pair skips this)
3. Detects the LAN IPv4 (Bonjour; not link-local `fe80`)
4. Verifies battery over Wi‑Fi and shows Home Assistant `entity_id`s
5. Saves the device — unplug USB. Daily use is Wi‑Fi only

Repeat **+ Add** for each hub (phone, iPad, …).

### 3. Home Assistant entities

MQTT discovery creates, per hub and per accessory that reports battery:

- `sensor.idevice_<key>_battery`
- `sensor.idevice_<key>_battery_state`

`<key>` is a stable short id from the device UDID. Expand a card in the UI to
copy the real `entity_id`.

A JSON snapshot is still written to `/share/idevice_battery.json` (optional
`command_line` package in `homeassistant/packages/` if you do not want MQTT).

### 4. Dashboard (optional)

See `homeassistant/lovelace/bubble_card_example.yaml`. Prefer the MQTT
`sensor.idevice_*` entities over the old `command_line` sensors.

## Charging state

Apple may report `BatteryIsCharging: false` while the cable is connected
(`ExternalConnected: true`) during **Optimized Battery Charging** (~80% hold).

Hub mapping:

| Condition | `battery_state` |
|-----------|-----------------|
| `FullyCharged` | `full` |
| `BatteryIsCharging` **or** `ExternalConnected` | `charging` |
| otherwise | `Not Charging` |

Accessories use companion `BatteryIsCharging` only.

UI battery bar: **≤20% red**, **≤30% orange**, **>30% green**. Charging is
always green, with a plug icon next to the percentage.

## Sleep / reachability

When the hub sleeps, Bonjour RemotePairing and lockdown `:62078` often
disappear. The poll fails; last known values are kept. Unlock the device
(Wi‑Fi on) and tap **↻**, or wait one poll cycle.

Accessory scans can skip a round while the hub is locked; last known accessory
values stay on screen.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Pairing](docs/PAIRING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

Published at [eMMeCodes/iDeviceBattery](https://github.com/eMMeCodes/iDeviceBattery).
Push to `main` builds `ghcr.io/emmecodes/idevice-battery` (`aarch64` + `amd64`).

## License

MIT — see [LICENSE](LICENSE).

`pymobiledevice3` and Apple protocols are third-party; this project only
orchestrates them for Home Assistant.
