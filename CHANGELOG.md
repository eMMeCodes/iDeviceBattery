# Changelog

## 0.9.2 — 2026-08-31

- Add-on info `url` → https://github.com/eMMeCodes/iDeviceBattery (link in Supervisor UI)
- Slug migration `idevice_pair` → `idevice_battery`
- Seed device registry from `/share/idevice_battery.json`; lockdown backup/restore via share
- AppArmor: S6 `/init` + dual profile (`local_idevice_battery` / `idevice_battery`)
- Watchdog URL uses `[HOST]:[PORT:8109]`; map `homeassistant_config:ro`
- Dockerfile: default `BUILD_ARCH=aarch64` (silence CI lint warning)

## 0.9.0 — 2026-08-31

- Ingress UI: compact cards, fold Device / Accessories, copy `entity_id`
- Battery bar: ≤20% red, ≤30% orange, >30% green; charging = green + plug icon
- Last check as relative time (`3 minutes ago`)
- Config: only `poll_minutes` (menu 1–10, default 3)
- MQTT discovery is the primary HA path; no MQTT / UDID fields in add-on options
- ProductType map (~238 models) for friendly names
- Drop unused wizard preview page and leftover UI CSS

## 0.8.x — 2026-08-31

- Multi-device registry, Wi‑Fi IPv4 auto-detect (Bonjour)
- Accessory Discover / Check now
- Ingress overlay `/share/idevice_ui` for live UI without image rebuild

## 0.6.1 — 2026-08-30

- Ingress web UI (port 8109): paired device list + values
- **Add** wizard: unlock → USB → Trust → RemotePairing → IP → verify → save
- Device registry in `/data/devices.json` (seeds from legacy options)
- Poller supports registry; keeps legacy `phone`/`watch` JSON for existing sensors

## 0.5.3 — 2026-08-29

- Read options from `/data/options.json` (no `hassio_api` / bashio Supervisor calls)
- Keep `build-essential` so `sslpsk-pmd3` builds on aarch64

## 0.5.2 — 2026-08-29

- Remove experimental `comptest` / classic companion Wi-Fi probes from the image
- Add-on options: `phone_udid`, `phone_host`, `poll_seconds`
- Fail fast if UDID/host are empty
- Documentation and git-ready packaging

## 0.5.1 — 2026-08-29

- Phone `battery_state`: treat `ExternalConnected` as `charging` (Optimized Battery Charging hold)

## 0.5.0 — 2026-08-29

- Production poller: phone lockdown Wi-Fi + Watch RemotePairing userspace RSD
- Retain last good phone/watch on partial failure
- Write `/share/idevice_battery.json`
- `boot: auto`, `host_network`, `share:rw`

## 0.4.x

- Exploration builds proving RemotePairing Wi-Fi Watch path; discarded netmuxd / classic companion Wi-Fi
