# Changelog

## 0.9.25 — 2026-09-04

- Two roles only: **device** (paired iPhone/iPad) and **accessory** (Watch, AirPods, …)
- Each node has `kind` (`iphone`, `watch`, `airpods`, …) plus `stale` / `updated_at`
- Watch is no longer a special slot; it is an accessory with `kind=watch`
- JSON still writes legacy `hub` / `watch` / `phone` aliases for older sensors

## 0.9.24 — 2026-09-04

- Remove unused USB Re-pair / diagnose API and dead UI CSS (folders, chevrons, tips)
- Discover now sets `watch_stale` / `watch_updated_at` like a normal poll
- English-only comments and user-facing strings; log markers `HUB_OK` / `HUB_FAIL`

## 0.9.23 — 2026-09-04

- Remove USB Re-pair from device cards (Add wizard covers USB RemotePairing)
- Cards stay Discover + Remove only

## 0.9.22 — 2026-09-04

- Collapsed card: full-card hover + click to expand
- Discover on every device (scan for new accessories over Wi‑Fi)
- USB Re-pair only when RemotePairing record is missing (not for normal Discover)

## 0.9.21 — 2026-09-04

- Hover only on collapsed cards; remove expand chevrons

## 0.9.20 — 2026-09-04

- Closed-card hover covers the full card (not only the title button)

## 0.9.19 — 2026-09-03

- Remove divider above device info; remove accessory left border
- Hover feedback on expandable row and all buttons

## 0.9.18 — 2026-09-03

- Lift card greys so devices read clearly against the Web UI background; stronger zebra

## 0.9.17 — 2026-09-03

- Neutral grey palette: light zebra device cards; deeper inset layers when accessories present
- Color reserved for battery bar / status badges

## 0.9.16 — 2026-09-03

- Device info block grouped with matching accent fill/border
- Softer card and accessory borders

## 0.9.15 — 2026-09-03

- Device cards alternate green / amber tint; accessories stay blue

## 0.9.14 — 2026-09-03

- Accessory battery bar vertically centered like hub rows
- Accessory cards tinted (accent) to distinguish from device cards

## 0.9.13 — 2026-09-03

- Web UI: remove instructional status banners (RemotePairing / unlock / Discover hints)

## 0.9.12 — 2026-09-03

- Web UI: flat device expand (info + accessory rows, no Device/Accessories folders)
- Watch / accessories only on the hub that owns them (no global Watch on iPads)
- Hide empty accessory UI; Re-pair Watch only when relevant
- Remove tooltip titles

## 0.9.11 — 2026-09-03

- Detect missing RemotePairing record (`remote_<UDID>.plist`) — main cause of stuck Watch after reinstall
- Backup/restore RemotePairing plists under `/share/idevice_remotepairing_backup`
- Web UI **Re-pair Watch** (USB) + diagnose dump `/share/idevice_diag.json`

## 0.9.10 — 2026-09-03

- Mark Watch / accessories **stale** when RemotePairing fails (keep last-known %, show in UI)
- Do not republish stale Watch over MQTT while hub may still update
- Retry Bonjour RemotePairing browse a few times before giving up

## 0.9.9 — 2026-08-31

- **Local install:** remove `image:` from `/addons/idevice_battery/config.yaml` so Supervisor
  builds from source (GHCR image was stuck at 0.9.6)
- Web UI ↻ shows footer feedback (Updated vs no response / asleep)
- `/api/status` includes running add-on `version`

## 0.9.8 — 2026-08-31

- Fix manual ↻ refresh crash (`name 'result' is not defined`)
- Refresh hub Wi‑Fi IP from Bonjour **every poll** (not only on `.local`/IPv6)
- Retry lockdown `:62078` once after Bonjour host rediscovery on timeout
- Poll hubs in parallel (faster multi-device cycles)
- Lockdown connect timeout default 20 s (`IDEVICE_LOCKDOWN_TIMEOUT`)

## 0.9.7 — 2026-08-31

- Mark hub data **stale** when a poll fails but last-known values are kept (Web UI badge + per-device `hub_updated_at`)
- Do not republish stale hub state over MQTT
- Manual ↻ check uses the same poll path as the background loop

## 0.9.6 — 2026-08-31

- Store `logo.png` uses the same simple artwork as `icon.png`
- Single AppArmor profile (`idevice_battery`); Supervisor rewrites the runtime name
- Drop unused `homeassistant_config` mapping
- Back from Web UI prefers browser history (works after reinstall / GitHub slug)

## 0.9.5 — 2026-08-31

- Add-on store `icon.png` / `logo.png` artwork

## 0.9.4 — 2026-08-31

- Overlay UI `/share/idevice_ui` is opt-in
- Remove unused Python helpers; quieter companion failures

## 0.9.3 — 2026-08-31

- Ingress Web UI binds `0.0.0.0:8109`

## 0.9.2 — 2026-08-31

- Add-on info `url` → GitHub repo; slug `idevice_battery`
- Migrate devices/lockdown from `idevice_pair` via share

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
