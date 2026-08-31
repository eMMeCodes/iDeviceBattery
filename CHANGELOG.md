# Changelog

## 0.9.1 — 2026-08-31

- Publish-ready layout: `idevice_battery/` folder, `repository.yaml`, GHCR `image:`
- Multi-arch build (`aarch64`, `amd64`) via GitHub Actions
- AppArmor profile (`apparmor.txt`); Ingress UI binds `172.30.32.2:8109`
- Slug renamed `idevice_pair` → `idevice_battery` (matches product name)
- `stage: experimental`, watchdog, `translations/en.yaml`, per-app `DOCS.md`

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
- Ingress overlay `/share/idevice_ui` was a dev shortcut for UI tweaks without
  rebuilding the image; it is optional and not required for normal use.

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
