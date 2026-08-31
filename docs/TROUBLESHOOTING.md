# Troubleshooting

## Card / sensors show old values

1. Confirm `/share/idevice_battery.json` `ts` is recent
2. Unlock the iPhone — sleep often drops Bonjour + `:62078`
3. Wait one add-on poll (`poll_minutes`, default 3) plus one HA `scan_interval` (60)
4. Check `error` in the JSON

## Phone battery OK, Watch missing

| Symptom | Likely cause |
|---------|----------------|
| `no RemotePairing on Bonjour` | Phone asleep, or missing `remote_<UDID>.plist` |
| `no paired watches in companion registry` | Watch not paired to this iPhone |
| Tunnel / RSD errors | Transient Wi-Fi; retry next poll |

Re-pair RemotePairing over USB if the remote plist is gone.

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

Useful log markers: `PHONE_OK`, `REMOTEPAIRING`, `TUNNEL_OK`, `RSD_OK`, `WATCH_OK`,
`PHONE_FAIL`, `WATCH_FAIL`.
