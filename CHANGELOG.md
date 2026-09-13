# Changelog

## 0.9.29 — 2026-09-13

- Poll never hangs the loop: whole-cycle timeout, accessory timeout, JSON always rewritten (stale on failure)
- Watchdog: if the snapshot is not updated for two poll intervals, the add-on restarts
- Device % (iPhone/iPad, keyed by UDID) no longer waits on accessory tunnels
- Keep stored LAN IP when lockdown `:62078` is open; Bonjour without RemotePairing tunnels
- Accessories are whatever CompanionProxy lists (Watch, headphones, Pencil, …); all optional; re-scanned every poll
- `wifi-connections` enabled for every paired device, not only the first
- Battery JSON file lock; distinct MQTT client ids for UI vs poller
- Manual ↻ / Discover / wizard Wi‑Fi lookup time out instead of blocking forever

## 0.9.28 — 2026-09-04

- First public release
