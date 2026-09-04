# Changelog

## 0.9.28 — 2026-09-04

- Registry writes are locked; poll never re-adds a device you just Removed
- Snapshot / MQTT ignore UDIDs that are no longer in the registry
- No RemotePairing record (typical iPad): skip accessories quietly; device battery still polls
- Documentation: store install (My Home Assistant repository badge) and where to find the app

## 0.9.27 — 2026-09-04

- JSON is device/accessory only (no `hub` / `phone` / `watch` aliases)
- MQTT discovery is the only Home Assistant path
- Pin `pymobiledevice3==11.2.3` and `paho-mqtt==2.1.0`
- Hide header **+ Add** when the list is empty (empty state already has **Add device**)
- Removing the last device no longer restores the list from `/share/idevice_battery.json`

## 0.9.26 — 2026-09-04

- Device `battery_state`: `full` / `charging` only while power is connected
  (`FullyCharged` alone after unplug no longer shows as charging)
- Web UI: do not label `full` as “Charging”; plug icon only while charging

## 0.9.25 — 2026-09-04

- Roles: **device** (paired iPhone/iPad) and **accessory** (Watch, AirPods, …)
- Each node has `kind`, `stale`, and `updated_at`
- Watch is an accessory (`kind=watch`)

## 0.9.22 — 2026-09-04

- Discover on every device (Wi‑Fi accessory scan)
- USB pairing only via **+ Add** (Trust + RemotePairing)

## 0.9.11 — 2026-09-03

- Detect missing RemotePairing record (`remote_<UDID>.plist`)
- Backup/restore RemotePairing plists under `/share/idevice_remotepairing_backup`

## 0.9.10 — 2026-09-03

- Mark accessories **stale** when RemotePairing fails (keep last-known %)
- Do not republish stale accessories over MQTT
- Retry Bonjour RemotePairing browse before giving up

## 0.9.8 — 2026-08-31

- Refresh device Wi‑Fi IP from Bonjour every poll
- Retry lockdown `:62078` once after host rediscovery on timeout
- Poll devices in parallel; lockdown timeout default 20 s

## 0.9.7 — 2026-08-31

- Mark device data **stale** when a poll fails but last-known values are kept
- Do not republish stale device state over MQTT
- Manual ↻ uses the same poll path as the background loop

## 0.9.2 — 2026-08-31

- Add-on slug `idevice_battery`; Ingress UI on port 8109
- Migrate devices/lockdown from earlier add-on data via share

## 0.9.0 — 2026-08-31

- Multi-device registry; MQTT discovery as the HA path
- Config: only `poll_minutes` (1–10, default 3)
- ProductType map for friendly names; battery bar colors

## 0.6.1 — 2026-08-30

- Ingress UI: paired device list + **Add** wizard
  (USB → Trust → RemotePairing → Wi‑Fi IP → verify → save)

## 0.5.1 — 2026-08-29

- Device `battery_state`: power connected (`ExternalConnected` / charging) while
  FullyCharged → `full` / `charging` (Optimized Battery Charging hold)

## 0.5.0 — 2026-08-29

- Production poller: device lockdown Wi‑Fi + accessory RemotePairing RSD
- Retain last good values on partial failure
- Write `/share/idevice_battery.json`
