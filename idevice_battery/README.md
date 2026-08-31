# iDevice Battery (Home Assistant app)

See [DOCS.md](DOCS.md) for user-facing documentation.

Developer notes:

- Slug/folder `idevice_battery` matches the product name **iDevice Battery**.
- Published as [eMMeCodes/iDeviceBattery](https://github.com/eMMeCodes/iDeviceBattery).
- Local development: copy this folder to `/addons/idevice_battery` and **remove**
  the `image:` line from `config.yaml` so the Supervisor builds locally.
- Optional live UI overlay: `/share/idevice_ui/.enable` (or env `IDEVICE_UI_OVERLAY=1`).
  Without that flag the image `/www` is used.
