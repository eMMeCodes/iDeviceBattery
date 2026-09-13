# Home Assistant App: iDevice Battery

Pair an iPhone or iPad over USB once, then poll battery over Wi‑Fi and publish MQTT sensors to Home Assistant.

Accessories (Watch, AirPods, …) are read through the paired device.

The Home Assistant Companion app is not used.

This app is **experimental**.

## Installation

This app is published from a GitHub repository, not the official store.

Add the repository once, then install it like any other app.

1. Click the Home Assistant My button below to add this repository.

   [![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.][repo-badge]][repo]

2. If the dialog does not open, add the URL by hand:

   **Settings** → **Apps** → **App store** → **⋮** → **Repositories** → **Add**

   ```text
   https://github.com/eMMeCodes/iDeviceBattery
   ```

3. In the App store, find **iDevice Battery** and click **Install**.

4. Click **Start**. Check the logs if something looks off.

5. Open the app from **Settings** → **Apps** → **iDevice Battery**.

After install it sits with the other apps (Mosquitto, Studio Code Server, …):

**Settings** → **Apps** → **iDevice Battery**

From that page use **Open Web UI**.

The sidebar also gets an **iDevice Battery** item (Ingress).

You need an MQTT broker (Mosquitto or equivalent).

This app requests `mqtt:need`.

Host and credentials come from Supervisor.

Optional override file:

`/share/idevice_mqtt.json` — `host`, `port`, `username`, `password`

### Local build (development)

Copy `idevice_battery/` to `/addons/idevice_battery` and **remove** the `image:` line from `config.yaml` so Supervisor builds locally.

Install from **Local apps**.

## How to use

1. Open **Settings** → **Apps** → **iDevice Battery**, then **Open Web UI**.

2. Unlock the iPhone or iPad and plug it into the Home Assistant host with USB.

3. Tap **Add device** (or **+ Add** if you already have devices).

4. Tap **Trust** on the device if asked.

5. Wait until a LAN IP and a battery reading appear, then unplug.

Daily use is Wi‑Fi only.

Repeat for each iPhone or iPad.

If you reset Trust or Location & Privacy, erase the device, or wipe the app data, run the USB wizard again.

### Entities

MQTT discovery creates, per device and per accessory that reports a level:

- `sensor.idevice_<key>_battery` (`%`)
- `sensor.idevice_<key>_battery_state` (`charging` / `full` / `Not Charging`)

`<key>` is derived from the UDID.

Expand a card in the Web UI to copy the real `entity_id`.

`/share/idevice_battery.json` is a debug snapshot.

Dashboards should use the MQTT sensors.

## Configuration

**Note:** _Restart the app after changing the configuration._

Example app configuration:

```yaml
poll_minutes: 3
```

### Option: `poll_minutes`

How often paired devices are polled over Wi‑Fi.

Allowed values: `1`–`10` (minutes). Default: `3`.

## Charging state

Apple may report `BatteryIsCharging: false` while the cable is connected (`ExternalConnected: true`) during Optimized Battery Charging (~80% hold).

| Condition | `battery_state` |
|-----------|-----------------|
| Power connected and fully charged | `full` |
| Power connected, not full | `charging` |
| Cable unplugged | `Not Charging` |

Leftover `FullyCharged` after unplug is treated as **Not Charging**.

## Known issues and limitations

- **Home Assistant OS or Supervised only** (`aarch64`, `amd64`). Not Core or Container.
- **Sleep.** Deep sleep often closes port `:62078` and Bonjour. Last good values are kept; the card shows **Stale**; MQTT is not overwritten until the next successful read. Wake the device on Wi‑Fi (unlock is not required) and wait one poll, or tap **↻**.
- **Accessories.** Watch / headphones appear only if the paired device exposes them. No RemotePairing record (typical on some iPads): accessories are skipped quietly; device battery still works.
- **iOS / pymobiledevice3.** This path uses Apple protocols that can change without notice.

## Support

- [Open an issue][issue]
- [Architecture][architecture]
- [Pairing (CLI)][pairing]
- [Troubleshooting][troubleshooting]

## License

MIT. See the [LICENSE][license] file.

This app orchestrates [pymobiledevice3][pymobiledevice3] and Apple device protocols; those remain third-party.

[architecture]: https://github.com/eMMeCodes/iDeviceBattery/blob/main/docs/ARCHITECTURE.md
[issue]: https://github.com/eMMeCodes/iDeviceBattery/issues
[license]: https://github.com/eMMeCodes/iDeviceBattery/blob/main/LICENSE
[pairing]: https://github.com/eMMeCodes/iDeviceBattery/blob/main/docs/PAIRING.md
[pymobiledevice3]: https://github.com/doronz88/pymobiledevice3
[repo-badge]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[repo]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FeMMeCodes%2FiDeviceBattery
[troubleshooting]: https://github.com/eMMeCodes/iDeviceBattery/blob/main/docs/TROUBLESHOOTING.md
