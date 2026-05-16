# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- BLE `setup()` now returns `False` when agent or RFCOMM profile D-Bus registration fails, preventing silent timeouts during phone detection.

## [0.1.0] - 2026-05-16

### Added
- Full WAA pipeline: BLE advertising → WiFi AP credential push → OpenAuto projection with audio.
- BLE pairing agent (NoInputNoOutput) and RFCOMM profile for credential exchange.
- HFP HF profile registration for instant Android Auto reconnect without re-pairing.
- Auto-reconnect: pages last known phone on IDLE entry, retries every 30 s.
- Phone disconnect detection in `PROJECTION_ACTIVE` (stdout + stderr pattern match).
- BlueZ crash/restart detection via D-Bus `NameOwnerChanged`; exits cleanly for systemd restart.
- WiFi AP+STA mode support; `piauto-wifi.service` disconnects STA on failure to stabilise AP.
- GPIO ignition sensing (GPIO 17) and fan PWM stub (GPIO 4).
- Thermal monitoring with hysteresis-based fan profile.
- AVRCP volume polling with PipeWire/WirePlumber (`wpctl`) synchronisation.
- System clock persistence to `/data/clock` for monotonic time without RTC.
- Splash screen subprocess with DRM master handoff to OpenAuto.
- `bt_pair` CLI for Bluetooth speaker discovery and pairing.
- Touchscreen auto-detection for Qt EGLFS; mouse cursor hidden.
- `PIAUTO_NO_AP` and `PIAUTO_NO_HALT` safety env vars for development.

### Fixed
- `bt_pair` no longer calls `Connect()` after pairing to avoid BlueZ 5.82 SEGV on multi-profile devices.
- Reconnect loop cancelled before leaving IDLE state to prevent task leak.
- WiFi flush no longer kills active SSH or BLE D-Bus connections.
- GPIO `debounce_period` corrected for libgpiod v2 API.
- DRM card auto-detection and correct HDMI output selection.
- Touchscreen double-tap and return to splash on phone disconnect.
- Hardcoded default WiFi password removed; validated at config load.
- Volume module rewritten to use `dbus_next` async API (removed blocking subprocess calls).
- Consumer-grade reliability hardening: pending task cleanup in `PROJECTION_ACTIVE`, reconnect loop exception logging, OpenAuto dual-monitor guard, thermal sensor failure persistence, volume `wpctl` cleanup on `CancelledError`.

[Unreleased]: https://github.com/andrew-graydon/piauto/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/andrew-graydon/piauto/releases/tag/v0.1.0
