# Home Assistant App: iDevice Battery

Pair **iPhone / iPad** over USB once, then poll hub and accessory battery over Wi‑Fi
and publish MQTT discovery sensors to Home Assistant.

## Requirements

- Home Assistant OS or Supervised
- MQTT broker (the app requests `mqtt:need`)
- iPhone/iPad on the same LAN as Home Assistant
- One-time USB connection to the HA host (Trust + RemotePairing)

## Configuration

| Option | Description |
|--------|-------------|
| `poll_minutes` | Poll interval in minutes (menu **1–10**, default **3**) |

MQTT credentials are read from the Supervisor. Optional override:
`/share/idevice_mqtt.json` (`host`, `port`, `username`, `password`).

## Usage

1. Install the app from this repository.
2. Open **Open Web UI** (Ingress) or the sidebar panel **iDevice Battery**.
3. Tap **+ Add**, connect the device via USB, approve **Trust**, wait for LAN IP
   detection and battery verification, then unplug USB.
4. Entities appear via MQTT discovery (`sensor.idevice_*_battery`, etc.).

## Notes

- Accessories (Watch, headphones, …) appear only if the hub exposes them on the
  last scan; an empty accessory list is normal.
- When the hub sleeps, polling may fail until the device is unlocked on Wi‑Fi.
- A JSON snapshot is written to `/share/idevice_battery.json` for optional
  command_line sensors.

See the [repository README](https://github.com/eMMeCodes/iDeviceBattery)
for architecture, pairing details, and troubleshooting.
