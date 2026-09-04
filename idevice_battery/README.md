# Home Assistant App: iDevice Battery

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

## About

Pair an iPhone or iPad to the Home Assistant host over USB once (Trust + RemotePairing).
After that, battery is read over Wi‑Fi and published as MQTT discovery sensors.

Accessories (Watch, AirPods, and similar) are read through the paired device when it exposes them.
A device with no accessories is normal if none paired.

This app is experimental.
It needs Home Assistant OS or Supervised, an MQTT broker, and a one-time USB connection.
**Deep sleep often drops Wi‑Fi lockdown until the device wakes (the lock screen is fine).**
**Apple protocols can change with iOS.**

Open **Documentation** on this page for install, pairing, and options.

After install the app is at **Settings → Apps → iDevice Battery**.
Source and issues: [eMMeCodes/iDeviceBattery][github].

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[github]: https://github.com/eMMeCodes/iDeviceBattery
