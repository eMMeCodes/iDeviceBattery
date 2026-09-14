# Troubleshooting

## Device vs accessories (important)

| Target | Path | Unlock required? |
|--------|------|------------------|
| **iPhone / iPad battery** | Wi‑Fi lockdown TCP `:62078` + Trust plist, **or** RemotePairing RSD if that tunnel is up | **No** if the device is **awake on Wi‑Fi** (screen can be locked). Deep sleep closes `:62078`; RemotePairing may still work if an accessory wakes the phone. |
| **Watch / AirPods / …** | RemotePairing → RSD → `companion_proxy` | Device must be **reachable on Bonjour**; deep sleep often blocks RemotePairing. |

RemotePairing RSD is primarily how **accessories** are read through the paired device. From 0.9.30 the same tunnel is also used for the **device** `%` when `:62078` is closed.

## Card / sensors show old values

1. Confirm `/share/idevice_battery.json` `ts` is recent (it must move every poll, even when stale)
2. Wake the device on Wi‑Fi — sleep drops Bonjour + `:62078`
3. Wait one poll (`poll_minutes`, default 3)
4. Check per-device `stale` and `error` in the JSON
5. Stale devices publish MQTT **availability = offline** (HA shows **unavailable**). The last `%` stays retained in the broker but is not treated as current. `sensor.idevice_<key>_last_updated` keeps the last successful read.
6. If `ts` is hours old while the app is running, the poller hung; from 0.9.29 the add-on restarts itself after two missed polls

## Device battery OK, Watch missing

| Symptom | Likely cause |
|---------|----------------|
| `ACCESSORY_SKIP` / missing `remote_<UDID>.plist` | No RemotePairing record; device battery still works. USB **Add device** again. |
| `no RemotePairing on Bonjour` | Device asleep, or missing `remote_<UDID>.plist` |
| `no paired watches in companion registry` | Watch not paired to this iPhone |
| Tunnel / RSD errors | Transient Wi-Fi; retry next poll |

If `remote_<UDID>.plist` is missing, connect the device by USB and run **Add device** again.

## State stuck on `Not Charging` while plugged in

Raw Apple fields may show:

```json
"BatteryIsCharging": false,
"ExternalConnected": true
```

That is normal during **Optimized Battery Charging** (~80% pause).
The app maps power connected (`ExternalConnected` / `BatteryIsCharging`) →
`charging` or `full`. After unplug, leftover `FullyCharged` alone is **Not Charging**.

## MQTT sensors unknown

- MQTT broker running; this app has `mqtt:need`
- Expand the device card and copy the real `entity_id`
- Reload MQTT entities after a fresh pair if discovery lagged

## Classic companion / netmuxd experiments

Do not invest time in:

- Wi-Fi `companion_proxy` via classic StartService + raw TCP
- netmuxd as a substitute for Apple usbmuxd service ports
- CoreDeviceProxy Wi-Fi CDTunnel handshake (observed reset)

Those paths were evaluated and closed; use RemotePairing RSD.

## Logs

```bash
ha apps logs local_idevice_battery
cat /share/idevice_battery.json
```

Useful markers: `DEVICE_OK`, `DEVICE_FAIL`, `ACCESSORY_OK`, `ACCESSORY_SKIP`,
`ACCESSORY_FAIL`, `REMOTEPAIRING`, `TUNNEL_OK`, `RSD_OK`, `RSD_DEVICE`,
`[mqtt] published … stale→unavailable`.

### Web UI shows old battery / wrong “Charging”

If logs repeat `DEVICE_FAIL TimeoutError` the app keeps **last-known** device values.
The card shows **Stale** (not “Online”) and **Last updated** uses the timestamp of
the last successful device read, not the background poll clock.

Wake the device, keep it on Wi‑Fi, tap **↻**. A successful read logs `DEVICE_OK`.

“Charging” with the cable unplugged is often **stale** data from when
`ExternalConnected` was true; refresh after `DEVICE_OK` to clear it.
