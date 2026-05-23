# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Test suite: 53 tests covering config validation, BLE setup failure modes, and all state machine transitions (SM-001). Runs on dev machines with no Pi hardware via mocks.
- BT speaker auto-connect on every IDLE entry using `bluetooth.speaker_mac` from config (fire-and-forget, non-blocking).
- `save_speaker_mac()` in `config.py` to persist the last paired speaker MAC to `/data/piauto.yaml`.
- `BleManager._detect_adapter()` discovers the active BlueZ adapter at startup via ObjectManager — no more `hci0` hard-coding; works correctly when a USB dongle is also present.
- Splash process now exits cleanly via a `QUIT` stdin command processed in the Qt event loop, eliminating the "Splash killed (did not stop in 3 s)" warning on every connection cycle.
- `WifiConfig` now accepts `dhcp_start` / `dhcp_end` fields to override the hardcoded DHCP lease range for both standalone and AP+STA modes.

### Fixed
- BLE `setup()` now returns `False` when agent or RFCOMM profile D-Bus registration fails, preventing silent timeouts during phone detection.
- BT speaker pairing via the setup UI now saves `speaker_mac` to `/data/piauto.yaml` so the speaker auto-connects on subsequent boots (last paired wins).
- `wait_for_client_leave_ap()` now requires two consecutive negative ARP polls before declaring the phone gone — prevents STALE ARP entries triggering a false-positive reconnect cycle.
- HFP SLC daemon thread keeps its 5 s socket timeout active after the AT handshake instead of blocking indefinitely, so the thread exits promptly on disconnect.
- Splash stdout is now pumped by a dedicated background task into an `asyncio.Queue`; the "SETUP" button press can no longer be silently dropped due to a missed `readline()` call.
- `_kill_stale_autoapp()` now polls `ss` and waits up to 10 s for port 5000 to be fully released before returning, preventing EADDRINUSE failures on rapid reconnect.
- Ignition callback `_on_ignition_off` is now guarded by a `_ignition_fired` flag so GPIO bounce or duplicate calls cannot trigger a double shutdown event.
- In AP+STA mode, `hostapd` now uses the channel `wlan0` is currently on (detected via `iw dev wlan0 info`) instead of the config value — uap0 and wlan0 share one radio so they must be on the same channel.
- Volume sync `_get_transports()` D-Bus calls are now wrapped in a 3 s `asyncio.wait_for`; a hung bus connection auto-reconnects instead of blocking the sync task indefinitely.
- MAC addresses are now normalised to uppercase at all save/extract points (`PairingStore.save_pairing`, `save_speaker_mac`, `_extract_device_info`) so D-Bus device paths are always valid.

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
