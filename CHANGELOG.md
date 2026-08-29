# Changelog

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
