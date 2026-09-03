# Troubleshooting

## Device vs accessories (important)

| Target | Path | Unlock required? |
|--------|------|------------------|
| **iPhone / iPad battery** | Wi‑Fi lockdown TCP `:62078` + Trust plist | **No** if the device is **awake on Wi‑Fi** (screen can be locked). Deep sleep closes `:62078`. |
| **Watch / AirPods / …** | RemotePairing → RSD → `companion_proxy` | Hub must be **reachable on Bonjour**; deep sleep often blocks RemotePairing. |

RemotePairing RSD reads **accessories through the hub** — not the hub’s own battery.

## Card / sensors show old values

1. Confirm `/share/idevice_battery.json` `ts` is recent
2. Wake the device on Wi‑Fi — sleep drops Bonjour + `:62078`
3. Wait one add-on poll (`poll_minutes`, default 3)
4. Check per-device `stale` and `error` in the JSON (`hub_stale` is a legacy alias)

## Phone battery OK, Watch missing

| Symptom | Likely cause |
|---------|----------------|
| `no RemotePairing on Bonjour` | Phone asleep, or missing `remote_<UDID>.plist` |
| `no paired watches in companion registry` | Watch not paired to this iPhone |
| Tunnel / RSD errors | Transient Wi-Fi; retry next poll |

If `remote_<UDID>.plist` is missing, connect the hub by USB and run **+ Add** again.

## State stuck on `Not Charging` while plugged in

Raw Apple fields may show:

```json
"BatteryIsCharging": false,
"ExternalConnected": true
```

That is normal during **Optimized Battery Charging** (~80% pause).
Add-on ≥ 0.5.1 maps `ExternalConnected` → phone `charging`.

## `ha core check` OK but sensors unknown

- Ensure Core can read `/share` (HA OS mounts share into Core)
- Confirm package include for the `command_line` YAML
- Reload / restart Core after adding new `command_line` sensors

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

Useful log markers: `DEVICE_OK`, `DEVICE_FAIL`, `ACCESSORY_OK`, `ACCESSORY_FAIL`,
`REMOTEPAIRING`, `TUNNEL_OK`, `RSD_OK`, `[mqtt] skip stale device`.
Older logs used `HUB_*` / `PHONE_*` (same meaning).

### Web UI shows old battery / wrong “Charging”

If logs repeat `DEVICE_FAIL TimeoutError` the add-on keeps **last-known** device values.
From 0.9.7 the card shows **Stale** (not “Online”) and **Last updated** uses the
timestamp of the last successful device read, not the background poll clock.

Unlock the device, keep it on Wi‑Fi, tap **↻**. A successful read logs `DEVICE_OK`.

“Charging” with the cable unplugged is often **stale** data from when
`ExternalConnected` was true; refresh after `DEVICE_OK` to clear it.

Useful accessory markers: `ACCESSORY_OK`, `ACCESSORY_FAIL`.
