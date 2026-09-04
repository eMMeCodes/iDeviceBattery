# Home Assistant App: iDevice Battery

[![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.][repo-badge]][repo]

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Pair an iPhone or iPad to the Home Assistant host over USB once (Trust + RemotePairing).
After that, battery is read over Wi‑Fi and published as MQTT discovery sensors.

Accessories (Watch, AirPods, and similar) are read through the paired device when it exposes them.
A device with no accessories is normal if none paired.

This app is experimental.
It needs Home Assistant OS or Supervised, an MQTT broker, and a one-time USB connection.
**Deep sleep often drops Wi‑Fi lockdown until the device wakes (the lock screen is fine).**
**Apple protocols can change with iOS.**

## Installation

Same path as any third-party Home Assistant app: add the repository, then install.

1. Click the My button above, or add the URL by hand:

   **Settings** → **Apps** → **App store** → **⋮** → **Repositories** → **Add**

   ```text
   https://github.com/eMMeCodes/iDeviceBattery
   ```

2. In the App store, find **iDevice Battery** → **Install** → **Start**.
3. Open it from **Settings** → **Apps** → **iDevice Battery**.

You need an MQTT broker (`mqtt:need`).
Pair each device with USB via **Add device** in **Open Web UI**.

Full usage, options, and limitations: [DOCS.md](idevice_battery/DOCS.md).
That file is the same text as the app **Documentation** tab.

## How it works

| | Path |
| --- | --- |
| iPhone / iPad | Wi‑Fi lockdown (`:62078`) + Trust record → `com.apple.mobile.battery` |
| Watch / AirPods / … | RemotePairing (same USB session) → RSD → `companion_proxy` |

The classic AirBattery stack (Apple usbmuxd + Wi‑Fi companion) does **not** work on Linux.
This app uses RemotePairing + RSD.

## Layout

```text
idevice_battery/     Home Assistant app (config, image, Web UI)
docs/                Architecture, pairing CLI, troubleshooting
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Pairing (CLI)](docs/PAIRING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Changelog](CHANGELOG.md)

## License

MIT — [LICENSE](LICENSE).

`pymobiledevice3` and Apple protocols are third-party.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[repo-badge]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[repo]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FeMMeCodes%2FiDeviceBattery
