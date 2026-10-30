# Changelog

## 0.9.33 — 2026-10-30

- A Watch poll runs even when the previous RemotePairing handshake is still closing. That new attempt is what wakes a sleeping iPhone; skipping it left the last `%` on the card for hours
- The tunnel handshake has its own deadline and still closes the socket when it finishes, so the file-descriptor fix stays

## 0.9.32 — 2026-10-26

- Poll opens one RemotePairing socket, not every Bonjour address. The old path kept dozens of SSL sockets after a 3s close and the process died with `Too many open files` on `devices.lock` / `battery.lock`, so MQTT stayed unpublished and the sensors sat on `unavailable`
- A poll timeout no longer cancels the Apple handshake. The socket is closed when the handshake finishes, including when the iPhone is asleep and `:62078` never answers
- RemotePairing tries the saved Wi‑Fi address first, then at most one other IPv4
- An accessory missing from one CompanionProxy reply stays on the card as stale
- Paired phones are polled one at a time, so one hung tunnel cannot cancel the other
- If open files still climb past 512, the poller exits and the app restarts instead of crashing inside the lock

## 0.9.31 — 2026-09-14

- A stale poll keeps the last known `%` on the card instead of hiding it: freshness is the `stale` attribute plus `sensor.idevice_<key>_last_updated`, not a hole where the battery used to be
- New option `stale_behavior`: `last_known` (default) or `unavailable` for the 0.9.30 behaviour
- The `%` is republished every poll, so `expire_after` now only hides the sensors when the app really stops publishing
- Lovelace example shows **Last updated** next to the percentage
- Tests cover both stale modes and the never-read case

## 0.9.30 — 2026-09-14

- MQTT: stale polls mark sensors **unavailable** (availability topic + `last_updated` diagnostic). Last % stays in the broker but is no longer shown as live
- iPhone/iPad % from the RemotePairing RSD tunnel when lockdown `:62078` is closed but accessories still answer
- One RemotePairing host per poll (Bonjour duplicates are closed)
- Web UI only from Home Assistant Ingress (`172.30.32.0/23`) and localhost
- MQTT broker host from Supervisor `services/mqtt` (fallback `127.0.0.1` on `host_network`)
- Cache-bust query uses the running app version; drop extra `changelog:` config key
- CI unit tests: JSON lock, poll timeout, MQTT stale plan, Ingress allowlist

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
