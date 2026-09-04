# Architecture

## Goal

Expose the same battery numbers AirBattery shows for an iPhone and its paired
Apple Watch, on **Home Assistant OS (Linux / Raspberry Pi)**, over Wi-Fi after a
one-time USB pairing.

## Terminology

| Term | Meaning |
|------|---------|
| **device** | Paired iPhone / iPad (lockdown battery over Wi‑Fi) |
| **accessory** | Watch, AirPods, … exposed by that device via CompanionProxy |
| **kind** | Product class (`iphone`, `watch`, `airpods`, …) |

## What AirBattery does on macOS

Rough pipeline ([lihaoyun6/AirBattery](https://github.com/lihaoyun6/AirBattery)):

1. Discover phone via usbmux (`idevice_id -n` / Wi-Fi mux)
2. Phone battery via lockdown `ideviceinfo` / battery domain
3. Watch via `com.apple.companion_proxy` (host → usbmux → phone → Watch)

That stack rides **Apple usbmuxd**. There is no public Apple usbmuxd for Linux.

## What fails on Linux / HA OS

| Approach | Result |
|----------|--------|
| Debian `usbmuxd` | USB devices only |
| Classic `companion_proxy` over Wi-Fi lockdown `StartService` | Port returned; naked TCP connect refused |
| netmuxd bridging to `:62078` | Mux connect OK; ephemeral service ports still refused |
| CoreDeviceProxy lockdown Wi-Fi + CDTunnel | Port opens; handshake reset / connection lost |
| USB RSD `companion list --userspace` | Works (needs cable) |

## Production path (this add-on)

```
HA add-on (host_network)
  │
  ├─ Device battery (iPhone / iPad)
  │    TCP device:62078 + /data/lockdown/<UDID>.plist
  │    → create_using_tcp → domain com.apple.mobile.battery
  │
  └─ Accessory battery (Watch, …)
       Bonjour RemotePairing service for device UDID
       → userspace CDTunnel (pymobiledevice3)
       → RemoteServiceDiscovery (RSD)
       → CompanionProxyService
       → BatteryCurrentCapacity / BatteryIsCharging
```

Pair records (persistent in add-on `/data`):

| File | Purpose |
|------|---------|
| `/data/lockdown/<UDID>.plist` | Classic lockdown Trust |
| `/data/.pymobiledevice3/remote_<UDID>.plist` | RemotePairing (from `lockdown remotepairing --pair`) |

`HOME=/data` so pymobiledevice3 stores RemotePairing under the add-on data volume.

## Output contract

File: `/share/idevice_battery.json` (debug snapshot).

- Updated every `poll_minutes` (default 3 → 180 s)
- On partial failure, previous device / accessory objects are retained
- `error` string lists device failures; accessory issues are a soft note
- `path` is always `remotepairing-userspace-rsd`
- Each item in `devices[]` is a **device** with an `accessories[]` list

MQTT discovery (the Home Assistant path): `sensor.idevice_<udid-key>_battery` and
`_battery_state` for each device and each accessory that reports a level.

## Home Assistant side

MQTT entities only. Copy `entity_id`s from the Ingress UI.

## Non-goals

- Daily USB
- Replacing Companion for the iPhone itself (Companion phone battery still works)
- Implementing a custom Watch BLE protocol
- Running Apple usbmuxd on Linux
