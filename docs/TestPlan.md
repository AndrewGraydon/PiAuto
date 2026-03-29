# Test Plan: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-TP-001                |
| Version        | 4.0                          |
| Date           | 2026-03-28                   |
| Status         | Active                       |

## 1. Introduction

### 1.1 Purpose

This document defines the verification approach for every requirement in PiAuto-SRS-001. Each test case specifies what is being tested, the method, the pass/fail criteria, and the equipment needed.

### 1.2 Verification Methods

| Method          | Code | Description                                              |
| :-------------- | :--- | :------------------------------------------------------- |
| Test            | T    | Execute the system and observe behavior against criteria |
| Measurement     | M    | Use instruments or scripts to quantify a metric          |
| Demonstration   | D    | Show that a feature works in a realistic scenario        |
| Inspection      | I    | Review configuration files, logs, or code                |

### 1.3 References

| ID              | Document                         |
| :-------------- | :------------------------------- |
| PiAuto-SRS-001  | Software Requirements Specification |
| PiAuto-RTM-001  | Requirements Traceability Matrix |
| PiAuto-SM-001   | State Machine Specification      |

### 1.4 Test Equipment

| Item                              | Purpose                                          |
| :-------------------------------- | :----------------------------------------------- |
| Android phone (Android 11+, WAA-capable) | AA source device                          |
| Bluetooth speaker or audio receiver | A2DP audio verification                        |
| PiAuto hardware assembly (per BOM) | System under test                               |
| Laptop with SSH access to Pi      | Log inspection, script execution                 |
| Stopwatch or timing script        | Performance measurements                         |
| Multimeter                        | GPIO level verification                          |

---

## 2. Test Cases — Bluetooth & Discovery

### TC-001: WAA BLE Service Advertisement

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-001                                                  |
| Method       | T                                                       |
| Precondition | PiAuto booted, in IDLE state                            |
| Procedure    | 1. On Android phone, use a BLE scanner app (e.g., nRF Connect). 2. Scan for BLE devices. 3. Verify PiAuto appears with WAA service UUID `9b3f6c10-a4d2-418e-a2b9-0700300de8f4`. 4. On Pi, verify via `journalctl -u piauto` that BLE advertisement is registered. |
| Pass Criteria| WAA service UUID visible in BLE scan. Pi is advertising in BLE mode and RFCOMM profile is registered. |

### TC-002: BLE Discovery & RFCOMM Pairing

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-002, NR-005                                          |
| Method       | D                                                       |
| Precondition | PiAuto in IDLE, phone not previously paired              |
| Procedure    | 1. On phone, enable Wireless Android Auto in Settings. 2. Phone discovers PiAuto via BLE. 3. Phone pairs over Classic BT (auto-accepted). 4. Phone connects to RFCOMM profile. 5. Observe Pi transitions from IDLE → BT_PAIRING. 6. Verify via `journalctl` that RFCOMM NewConnection was received. |
| Pass Criteria| Pairing succeeds. RFCOMM connection received. State machine enters BT_PAIRING. |

### TC-003: RFCOMM Credential Exchange

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-003                                                  |
| Method       | T                                                       |
| Precondition | Phone paired, system in BT_PAIRING state, RFCOMM connected |
| Procedure    | 1. Monitor piauto logs for RFCOMM message exchange. 2. Verify WifiInfoResponse contains correct SSID, BSSID, password. 3. Verify WifiStartResponse contains correct IP and port (5000). |
| Pass Criteria| Protobuf messages exchanged via RFCOMM. State transitions to WIFI_WAIT. |

### TC-004: Pairing Record Persistence

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-004                                                  |
| Method       | T                                                       |
| Precondition | Successful pairing completed                            |
| Procedure    | 1. Reboot the Pi. 2. Inspect `/data/bt/` for stored pairing record. 3. Pair a second phone. Verify both records exist. 4. Pair 9 phones total. Verify oldest is evicted and only 8 remain. |
| Pass Criteria| Pairing records survive reboot. Max 8 records stored. FIFO eviction. |

### TC-005: Auto-Reconnect to Last Phone

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-005, NR-004                                          |
| Method       | D                                                       |
| Precondition | Phone previously paired and connected successfully       |
| Procedure    | 1. Reboot Pi. 2. Ensure phone BT and WiFi are ON, in range. 3. Wait for Pi to reach IDLE. 4. Observe automatic connection without any user interaction. |
| Pass Criteria| System automatically connects and reaches PROJECTION_ACTIVE without user interaction. |

---

## 3. Test Cases — Wi-Fi Access Point

### TC-006: AP Configuration

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-006, FR-007, FR-008, FR-009                          |
| Method       | T                                                       |
| Precondition | System in WIFI_WAIT state                               |
| Procedure    | 1. On Pi, run `iw dev wlan0 info` to verify 5 GHz channel (149 or 165). 2. Run `hostapd_cli status` to verify WPA2-AES. 3. Verify Pi interface has IP 192.168.1.1 via `ip addr`. 4. On phone or laptop, scan for SSID. |
| Pass Criteria| AP on 5 GHz, channel 149 or 165, WPA2-AES, correct SSID, Pi at 192.168.1.1. |

### TC-007: DHCP Lease Assignment

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-010                                                  |
| Method       | T                                                       |
| Precondition | AP running, phone joining AP                            |
| Procedure    | 1. Phone connects to PiAuto AP. 2. On Pi, inspect dnsmasq lease file or logs. 3. Verify phone received IP in 192.168.1.100–199 range. |
| Pass Criteria| Phone receives a DHCP lease within the specified range. |

---

## 4. Test Cases — Connection & Projection Tunnel

### TC-008: TCP Listen on Port 5000

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-011                                                  |
| Method       | T                                                       |
| Precondition | OpenAuto started, system in TCP_CONNECT                 |
| Procedure    | 1. On Pi, run `ss -tlnp | grep 5000`. 2. Verify OpenAuto is listening. |
| Pass Criteria| TCP socket bound to 192.168.1.1:5000 in LISTEN state.  |

### TC-009: TLS, Version Negotiation & Service Discovery

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-012, FR-013, FR-014                                  |
| Method       | T                                                       |
| Precondition | Phone connected to AP, OpenAuto running                  |
| Procedure    | 1. Monitor OpenAuto logs for TLS completion, version negotiation, and service discovery messages. 2. Verify TLS 1.2+ used. 3. Verify services negotiated: Media Sink, Input Source, Sensor Source. |
| Pass Criteria| TLS handshake succeeds. Version negotiated. Services discovered. Projection starts. |

### TC-010: Reconnection on Connection Loss

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-015, NR-001                                          |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE                                       |
| Procedure    | 1. Briefly toggle phone Wi-Fi OFF then ON. 2. Observe OpenAuto exits, state machine enters ERROR_RECOVERY. 3. Observe retry (up to 3 attempts — OpenAuto relaunched). 4. If phone reconnects within retry window, verify projection resumes. 5. Repeat with 4+ failures — verify system returns to IDLE. |
| Pass Criteria| Transient loss: projection resumes within 3 retries. Persistent loss: gracefully returns to IDLE after 3 retries. |

---

## 5. Test Cases — Video

### TC-011: H.264 Decode and Display

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-016, FR-017, FR-018, FR-019                          |
| Method       | D                                                       |
| Precondition | PROJECTION_ACTIVE, phone running AA with Maps or media  |
| Procedure    | 1. Observe AA UI on 7" display. 2. Verify rendering fills the 800×480 display. 3. Verify no tearing, corruption, or blank frames. 4. Verify no X11 or Wayland is running (`ps aux | grep -E 'Xorg|wayland'`). |
| Pass Criteria| AA UI renders correctly at 800×480 via Qt EGLFS. Video is smooth at 30 FPS. No compositor running. |

---

## 6. Test Cases — Audio

### TC-012: PipeWire Audio Path

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-020                                                  |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE, BT speaker connected                 |
| Procedure    | 1. On Pi, run `wpctl status`. 2. Verify PipeWire is the active audio server. 3. Verify OpenAuto audio streams are connected. |
| Pass Criteria| PipeWire running. Audio nodes connected. No ALSA or PulseAudio fallback. |

### TC-013: Four Audio Streams with Correct Parameters

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-021, FR-022, FR-023                                  |
| Method       | D + I                                                   |
| Precondition | PROJECTION_ACTIVE, BT speaker connected                 |
| Procedure    | 1. Play music (Media stream — 48 kHz stereo). 2. Start navigation (Guidance stream — 16 kHz mono, nav prompt audible). 3. Trigger a system notification (System Audio — 16 kHz mono). 4. Make a phone call (Telephony — 16 kHz mono). 5. Inspect PipeWire node properties via `pw-cli list-objects` to verify sample rates. |
| Pass Criteria| All four streams produce audible output. Media is 48 kHz stereo; others are 16 kHz mono (upsampled by PipeWire to match A2DP output). |

### TC-014: Audio Focus / Ducking

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-024                                                  |
| Method       | D                                                       |
| Precondition | PROJECTION_ACTIVE, music playing, BT speaker connected  |
| Procedure    | 1. Play music. 2. Trigger navigation prompt — verify music volume drops (~20%). 3. Make a phone call — verify music mutes entirely. 4. End call — verify music resumes. |
| Pass Criteria| Ducking and muting behavior matches AA audio focus protocol (GAIN_TRANSIENT_MAY_DUCK for nav, GAIN for telephony). |

### TC-015: BT A2DP Audio Output

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-025                                                  |
| Method       | D                                                       |
| Precondition | BT speaker paired and connected, PROJECTION_ACTIVE      |
| Procedure    | 1. Play music via AA. 2. Verify audio comes from BT speaker. |
| Pass Criteria| Audio is audible on BT speaker. No audio on HDMI or headphone jack. |

### TC-016: BT Audio Auto-Reconnect

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-026                                                  |
| Method       | T                                                       |
| Precondition | BT speaker previously paired                            |
| Procedure    | 1. Power cycle both Pi and BT speaker. 2. Wait for Pi to reach IDLE. 3. Run `wpctl status` — verify BT speaker is default sink. |
| Pass Criteria| BT speaker automatically becomes the default audio sink. |

---

## 7. Test Cases — Touch Input

### TC-017: Touch Event Path

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-027, FR-028, FR-029, FR-030                          |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE                                       |
| Procedure    | 1. Touch the display — verify AA responds (button press, map pan). 2. Touch all four corners — verify full screen area is responsive. 3. If debug logging available, verify touch coordinates span 0–10,000 range. |
| Pass Criteria| AA responds to all touch areas. Full display area maps correctly. |

### TC-018: Multi-Touch

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-031                                                  |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE, Maps open                            |
| Procedure    | 1. Pinch-to-zoom on Google Maps. 2. Verify map zooms in/out. |
| Pass Criteria| Pinch-to-zoom gesture recognized. Map responds correctly. |

---

## 8. Test Cases — Power Management

### TC-019: Ignition Sense Detection

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-032                                                  |
| Method       | M                                                       |
| Precondition | System running, GPIO 17 connected to ignition sense     |
| Procedure    | 1. Measure GPIO 17 — verify HIGH (3.3 V) with ignition ON. 2. Turn ignition OFF — verify LOW (0 V). 3. Pulse LOW for < 500 ms — verify system does NOT shut down (debounce). |
| Pass Criteria| GPIO 17 reflects ignition state. Debounce rejects pulses < 500 ms. |

### TC-020: Clean Shutdown

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-033                                                  |
| Method       | M                                                       |
| Precondition | System in PROJECTION_ACTIVE                             |
| Procedure    | 1. Turn ignition OFF. 2. Time from ignition OFF to system halt. 3. Verify < 10 s. 4. Verify no filesystem corruption on next boot. |
| Pass Criteria| Shutdown < 10 s. Clean boot afterward.                  |

### TC-021: Auto-Boot on Power

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-034                                                  |
| Method       | T                                                       |
| Precondition | System powered off                                      |
| Procedure    | 1. Apply 5.1 V power. 2. Verify Pi boots and reaches IDLE. |
| Pass Criteria| Pi boots automatically without button press. Reaches IDLE. |

---

## 9. Test Cases — Thermal Management

### TC-022: Fan PWM Control

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-035                                                  |
| Method       | M                                                       |
| Precondition | Fan connected to GPIO 4 circuit                         |
| Procedure    | 1. At idle (< 50 °C) — verify fan is OFF. 2. Run `stress-ng --cpu 4`. 3. At 50–65 °C — verify fan at 50% speed. 4. At > 65 °C — verify fan at full speed. 5. Stop stress — verify fan ramps down with 3 °C hysteresis. |
| Pass Criteria| Fan follows thermal profile. Hysteresis prevents rapid cycling. |

---

## 10. Test Cases — User Interface

### TC-023: Splash Screen

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-036, FR-037                                          |
| Method       | D                                                       |
| Precondition | Pi booted                                               |
| Procedure    | 1. Observe display during IDLE — verify "Waiting for phone" splash. 2. Initiate phone connection — verify status updates. 3. Verify smooth transition from splash to AA video on PROJECTION_ACTIVE (splash app exits, OpenAuto takes over display). |
| Pass Criteria| Splash displays correct status. Seamless transition to AA. No flicker or blank frames between splash and OpenAuto. |

---

## 11. Test Cases — Day/Night Mode

### TC-024: Night Mode Sensor

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-038, FR-039                                          |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE                                       |
| Procedure    | 1. Verify AA UI shows appropriate theme (day or night) based on phone's ambient light sensor. 2. Cover phone's light sensor — verify AA UI switches to night mode. 3. Uncover — verify switch to day mode. |
| Pass Criteria| AA theme follows phone's light sensor. Pi does not override. |

---

## 12. Test Cases — Filesystem & Storage

### TC-025: Read-Only Root & Writable /data

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-044, FR-045                                          |
| Method       | I + T                                                   |
| Precondition | System booted                                           |
| Procedure    | 1. Run `mount | grep " / "` — verify `ro` or overlayfs. 2. Attempt `touch /test` — verify ephemeral. 3. Write to `/data/test` — verify persists across reboot. 4. Pull power without shutdown. 5. Boot and verify intact. |
| Pass Criteria| Root is read-only. `/data` writable and persistent. No corruption after power-pull. |

---

## 13. Test Cases — Performance

### TC-026: Boot Time

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | PR-001                                                  |
| Method       | M                                                       |
| Procedure    | 1. Apply power. 2. Time until splash screen appears (IDLE). 3. Repeat 5 times. |
| Pass Criteria| All 5 runs: boot-to-IDLE < 25 seconds.                 |

### TC-027: Projection Latency

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | PR-002                                                  |
| Method       | M                                                       |
| Procedure    | 1. Display a real-time clock on AA. 2. Camera captures both phone screen and Pi display simultaneously. 3. Measure time delta from captured frame. |
| Pass Criteria| Delta ≤ 200 ms across 10 measurements.                  |

### TC-028: Video Frame Rate

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | PR-003                                                  |
| Method       | M                                                       |
| Precondition | PROJECTION_ACTIVE, Maps or video content                |
| Procedure    | 1. Enable V4L2 decode stats or OpenAuto frame counter. 2. Measure FPS over 60-second window. |
| Pass Criteria| Sustained 30 FPS (the AA protocol maximum at 800×480).  |

### TC-029: Audio Latency

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | PR-004                                                  |
| Method       | M                                                       |
| Procedure    | 1. Play a sharp click/tone on AA. 2. Record phone and BT speaker simultaneously. 3. Measure delay in audio editor. |
| Pass Criteria| Audio delay ≤ 100 ms.                                   |

### TC-030: Connection Setup Time

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | PR-005                                                  |
| Method       | M                                                       |
| Procedure    | 1. From IDLE, initiate phone connection (previously paired). 2. Time from PhoneDetected event to PROJECTION_ACTIVE. 3. Repeat 5 times. |
| Pass Criteria| All 5 runs: BT detection to projection ≤ 15 seconds.    |

---

## 14. Test Cases — Reliability

### TC-031: Power-Loss Resilience

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | NR-002                                                  |
| Method       | T                                                       |
| Procedure    | 1. During PROJECTION_ACTIVE, pull power cable. 2. Restore power. 3. Verify clean boot. 4. Verify pairing records intact. 5. Repeat 5 times. |
| Pass Criteria| Clean boot all 5 times. No corruption. Pairing records intact. |

### TC-032: 12-Hour Soak Test

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | NR-003                                                  |
| Method       | T                                                       |
| Procedure    | 1. Connect phone, enter PROJECTION_ACTIVE. 2. Play music continuously. 3. Leave for 12 hours. 4. Verify projection active. 5. Check CPU temp < 80 °C. 6. Check logs for errors. |
| Pass Criteria| Still projecting after 12 hours. No throttling. No errors. |

### TC-033: Configuration and Logging Inspection

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | NR-006, NR-007                                          |
| Method       | I                                                       |
| Procedure    | 1. Inspect `/data/piauto.yaml` — verify all config present. 2. Run `journalctl -u piauto` — verify log output. 3. Verify no log files on SD card (journald ring buffer only). |
| Pass Criteria| Single config file. Logs in journald only.              |

---

## 15. Test Cases — Audio Output Resilience

### TC-034: BT Speaker Mid-Session Disconnect

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-040, FR-041                                          |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE, music playing on BT speaker          |
| Procedure    | 1. Power off the BT speaker mid-playback. 2. Verify system does not crash or hang. 3. Run `wpctl status` — verify audio routed to null sink. 4. Power on the BT speaker. 5. Wait up to 30 s. 6. Verify audio resumes on BT speaker automatically. |
| Pass Criteria| No crash. Audio silently drops to null sink. Speaker auto-reconnects and audio resumes. |

---

## 16. Test Cases — System Time

### TC-035: Clock Initialization Without RTC

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-042, FR-043                                          |
| Method       | T + I                                                   |
| Precondition | Pi with no RTC, `/data/clock` from a previous shutdown  |
| Procedure    | 1. Reboot the Pi. 2. Immediately check `date` — verify time ≥ value in `/data/clock`. 3. Verify TLS handshake succeeds during projection (self-signed cert, no clock-dependent validation). 4. Shut down cleanly. 5. Verify `/data/clock` updated with current timestamp. |
| Pass Criteria| System time monotonically increasing across boots. TLS works regardless of clock accuracy. `/data/clock` updated on shutdown. |

---

## 17. Test Cases — State Machine Timeouts

### TC-036: BT_PAIRING Timeout

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-002 (timeout path), SM §3.3                          |
| Method       | T                                                       |
| Precondition | System in IDLE, phone initiates BLE connection           |
| Procedure    | 1. Start RFCOMM credential exchange but block/delay phone response (e.g., airplane mode mid-pairing). 2. Wait 15 seconds. 3. Verify state machine transitions to IDLE (not ERROR_RECOVERY). 4. Verify BLE advertising resumes. |
| Pass Criteria| BT_PAIRING times out after 15 s. Returns to IDLE. No ERROR_RECOVERY entered. |

### TC-037: TCP_CONNECT Timeout

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-011 (timeout path), SM §3.5                          |
| Method       | T                                                       |
| Precondition | Phone on AP, OpenAuto launched                          |
| Procedure    | 1. Prevent phone from completing TCP connection (e.g., firewall port 5000 on phone). 2. Wait 30 seconds. 3. Verify state machine transitions to ERROR_RECOVERY. 4. Verify retry logic triggers (up to 3 retries). |
| Pass Criteria| TCP_CONNECT times out after 30 s. Enters ERROR_RECOVERY. Retries up to 3 times, then returns to IDLE. |

### TC-038: Boot Timeout

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | SM §3.1 (BootTimeout)                                   |
| Method       | T                                                       |
| Precondition | Simulate a stuck service during boot                    |
| Procedure    | 1. Disable a critical service (e.g., `systemctl mask bluetooth`). 2. Boot the Pi. 3. Wait 60 seconds. 4. Verify state machine triggers SHUTDOWN on BootTimeout. 5. Unmask the service and reboot — verify normal boot. |
| Pass Criteria| Boot timeout fires at 60 s. System shuts down cleanly. Normal boot works after fix. |

---

## 18. Test Cases — Configuration Handling

### TC-039: Missing/Corrupt Config File

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | NR-006, Implementation §4.2                             |
| Method       | T                                                       |
| Precondition | System powered off                                      |
| Procedure    | 1. Rename `/data/piauto.yaml` to `/data/piauto.yaml.bak`. 2. Boot the Pi. 3. Verify system boots with built-in defaults. 4. Verify warning logged to journald. 5. Restore config. 6. Write invalid YAML (e.g., `wifi.channel: 999`). 7. Boot — verify defaults used and warning logged. |
| Pass Criteria| Missing config: boots with defaults, logs warning. Invalid config: boots with defaults, logs validation error. No crash in either case. |

---

## 19. Test Cases — AVRCP Volume Sync

### TC-040: Phone Volume Controls PipeWire Output

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-020                                                  |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE, music playing on BT speaker          |
| Procedure    | 1. Press phone volume up — verify speaker gets louder. 2. Press phone volume down — verify speaker gets quieter. 3. On Pi, run `journalctl -u piauto --since '5 min ago' \| grep AVRCP` — verify volume sync log entries. 4. Run `wpctl get-volume @DEFAULT_AUDIO_SINK@` — verify value changes with phone volume. |
| Pass Criteria| Phone volume buttons change BT speaker output level. PipeWire sink volume tracks AVRCP 0–127 mapped to 0.0–1.0. |

---

## 20. Test Cases — BT Speaker Pairing UI

### TC-041: Touchscreen BT Speaker Scan and Pair

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-045, FR-046                                          |
| Method       | D                                                       |
| Precondition | System in IDLE, BT speaker in pairing mode, no speaker previously paired |
| Procedure    | 1. Tap "Setup" button on splash screen. 2. Verify BT setup UI appears. 3. Tap "Scan" — verify speaker appears in device list. 4. Tap the speaker entry — verify pairing succeeds (green confirmation). 5. Tap "Back" — verify return to idle splash. 6. Connect phone and play music — verify audio on newly paired speaker. |
| Pass Criteria| Speaker discovered, paired, and connected via touchscreen UI. Audio plays on speaker after AA connection. |

---

## 21. Test Cases — Phone Disconnect Recovery

### TC-042: Return to Splash on Phone Disconnect

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-038, SM §3.6                                         |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE                                       |
| Procedure    | 1. On phone, disconnect from Android Auto (Settings → Connected devices → Disconnect). 2. Verify Pi display returns to splash screen ("Waiting for phone...") within 10 s. 3. Verify `journalctl -u piauto` shows "Projection stopped detected" and transition to IDLE. 4. Reconnect phone — verify AA projection resumes. |
| Pass Criteria| Display returns to splash on disconnect. State machine transitions PROJECTION_ACTIVE → IDLE. Reconnection works without reboot. |

---

## 22. Test Cases — Touchscreen Input

### TC-043: Single-Tap Response (No Double-Tap Required)

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-027, FR-028                                          |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE                                       |
| Procedure    | 1. Single-tap an AA button (e.g., Home, Media). 2. Verify button activates on first tap. 3. Repeat on 5 different buttons across the screen. 4. Verify `QT_QPA_EGLFS_NO_LIBINPUT=1` is set in the OpenAuto process environment via `cat /proc/<pid>/environ \| tr '\0' '\n' \| grep LIBINPUT`. |
| Pass Criteria| All buttons activate on single tap. No double-tap needed. libinput disabled in process env. |

---

## 23. Test Cases — Dual BT Adapter

### TC-044: USB BT Dongle for Speaker Audio

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-025, NR-003                                          |
| Method       | T                                                       |
| Precondition | USB BT dongle inserted, BT speaker paired to hci1       |
| Procedure    | 1. Verify two adapters: `hciconfig -a` shows hci0 (built-in) and hci1 (USB). 2. Verify speaker connected via hci1: `bluetoothctl info 04:52:C7:8C:3D:CC` shows adapter path includes hci1. 3. Play music via AA for 5 minutes. 4. Verify no audio stuttering (WiFi and BT on separate radios). |
| Pass Criteria| Two BT adapters present. Speaker on USB adapter. No audio stuttering during WiFi+BT concurrent use. |

---

## 24. Test Cases — AP+STA Dual Interface

### TC-045: Simultaneous AP and Infrastructure WiFi

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-006                                                  |
| Method       | T                                                       |
| Precondition | uap0 virtual interface created by udev rule             |
| Procedure    | 1. Verify `ip link show uap0` exists and is UP. 2. Verify `iw dev uap0 info` shows AP mode. 3. Verify `iw dev wlan0 info` shows Managed (STA) mode. 4. From Pi, ping an external host (e.g., gateway) to confirm STA connectivity. 5. Simultaneously verify phone is connected to AP on uap0. 6. SSH to Pi over wlan0 while AA projection is active on uap0. |
| Pass Criteria| Both interfaces active simultaneously. AP serves phone on uap0. STA maintains infrastructure connectivity on wlan0. SSH works during projection. |

---

## 25. Test Cases — AndrewGraydon/openauto Fork Verification

### TC-046: aasdk Build with OpenSSL 3.x Patch

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | Implementation §2.4 (Patch #1)                          |
| Method       | I                                                       |
| Precondition | Pi running Debian 13 Trixie, `AndrewGraydon/aasdk` cloned |
| Procedure    | 1. Build aasdk per PiSetup §4.1. 2. Verify `cmake` and `make` complete with exit code 0. 3. Verify `libaasdk.so` installed. 4. Run `ldd /usr/local/lib/libaasdk.so | grep ssl` — verify links to libssl.so.3. |
| Pass Criteria| Build completes. Library installed. Links against OpenSSL 3.x. No deprecation errors. |

### TC-047: openauto Build with GSTVideoOutput and RtAudio 6.x Patches

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | Implementation §2.4 (Patches #2, #4, #5)               |
| Method       | I                                                       |
| Precondition | aasdk built (TC-046), `AndrewGraydon/openauto` cloned   |
| Procedure    | 1. Build openauto per PiSetup §4.2 with `-DGST_BUILD=TRUE`. 2. Verify `make` completes with exit code 0. 3. Verify `/usr/local/bin/autoapp` exists. 4. Run `ldd /usr/local/bin/autoapp | grep gst` — verify GStreamer libraries linked. 5. Confirm no `QGst` or `Qt5GStreamer` references in binary (`strings /usr/local/bin/autoapp | grep -i qgst`). |
| Pass Criteria| Build completes. GStreamer libraries linked. No QGst symbols in binary. |

### TC-048: GStreamer Pipeline Initialization

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | Implementation §2.4 (Patch #4), FR-016                 |
| Method       | T                                                       |
| Precondition | openauto binary built (TC-047)                          |
| Procedure    | 1. Start autoapp manually: `QT_QPA_PLATFORM=eglfs /usr/local/bin/autoapp`. 2. Monitor stderr for GStreamer pipeline log output. 3. Verify log shows `GSTVideoOutput: pipeline created` or equivalent. 4. Verify no `gst_parse_error` or `Could not link` errors. 5. Connect phone and start AA session — verify video pipeline starts. |
| Pass Criteria| GStreamer pipeline initializes without errors. Pipeline log shows decoder element chosen (v4l2h264dec or avdec_h264). |

### TC-049: Audio Stutter Regression Test (RtAudio Mutex)

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | KI-001, Implementation §2.4 (Patch #2)                 |
| Method       | D                                                       |
| Precondition | PROJECTION_ACTIVE, music playing via AA, BT speaker connected |
| Procedure    | 1. Play music (Media stream active). 2. Trigger Google Assistant / Gemini 5 times within 60 seconds (causes concurrent Guidance + Media stream access). 3. Trigger a navigation instruction while music is playing. 4. Listen for audio stutter on BT speaker. 5. Repeat with 3 concurrent triggers. |
| Pass Criteria| No audible stutter or audio dropout during concurrent stream access. RtAudio mutex prevents race condition. |

### TC-050: Hardware H.264 Decoder Selection (v4l2h264dec)

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-016, Implementation §2.4 (Patch #4)                 |
| Method       | T                                                       |
| Precondition | PROJECTION_ACTIVE, GSTVideoOutput pipeline running      |
| Procedure    | 1. Run `gst-inspect-1.0 v4l2h264dec` — verify element exists. 2. Check autoapp stderr for decoder selection log (e.g., `Using decoder: v4l2h264dec`). 3. Monitor CPU usage via `top` during projection — verify decode is hardware-accelerated (low CPU vs software decode). |
| Pass Criteria| `v4l2h264dec` found by GStreamer. Decoder log confirms hardware path. CPU usage < 40% during video decode. |

### TC-051: VideoWidget Frame Rendering (QPainter)

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-017, Implementation §2.4 (Patch #4)                 |
| Method       | D                                                       |
| Precondition | PROJECTION_ACTIVE with AndrewGraydon/openauto binary    |
| Procedure    | 1. Connect phone. 2. Verify AA UI renders on 7" display. 3. Verify video fills 800×480 area correctly (no black bars, no overscan, no undersized frame). 4. Verify touch input works (single tap activates AA buttons). 5. Verify no visual tearing or dropped frames. |
| Pass Criteria| AA video renders at correct size and aspect ratio. Touch works on first tap. No tearing. Display matches screen bounds. |

### TC-052: Full Projection Session — New Binary End-to-End

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-011–FR-031 (all projection requirements), KI-001, KI-002 |
| Method       | D                                                       |
| Precondition | AndrewGraydon/openauto binary installed at `/usr/local/bin/autoapp` |
| Procedure    | 1. Boot PiAuto. 2. Connect phone from scratch (BLE discovery → RFCOMM → WiFi join → AA start). 3. Verify video renders correctly (TC-051). 4. Verify touch input works (TC-043). 5. Play music and trigger Gemini — verify no stutter (TC-049). 6. Disconnect phone — verify return to splash (TC-042). 7. Reconnect — verify projection resumes. |
| Pass Criteria| Full session completes without regression. KI-001 (stutter) and KI-002 (video/touch) resolved. |

### TC-053: Software Fallback Decoder (avdec_h264)

| Field        | Value                                                   |
| :----------- | :------------------------------------------------------ |
| Traces to    | FR-016, Implementation §2.4 (Patch #4)                 |
| Method       | T                                                       |
| Precondition | openauto binary built with `-DGST_BUILD=TRUE`           |
| Procedure    | 1. Temporarily unload v4l2 decoder: `rmmod bcm2835-v4l2` (or confirm `v4l2h264dec` unavailable). 2. Start autoapp and connect phone. 3. Verify log shows `avdec_h264` selected as fallback. 4. Verify video renders (slower/higher CPU but functional). 5. Reload driver. |
| Pass Criteria| avdec_h264 fallback activates when v4l2h264dec unavailable. Video still renders (functional, not performance pass). |

---

## 26. Test Execution Checklist

| TC ID  | Description                       | Method | Status  | Date       | Notes |
| :----- | :-------------------------------- | :----- | :------ | :--------- | :---- |
| TC-001 | WAA BLE Advertisement             | T      | Pass    | 2026-03-28 | Phone discovers PiAuto via WAA BLE scan |
| TC-002 | BLE Pairing & Handshake           | D      | Pass    | 2026-03-28 | RFCOMM NewConnection received, IDLE → BT_PAIRING |
| TC-003 | WifiStartRequest Delivery         | T      | Pass    | 2026-03-28 | Credentials exchanged, state → WIFI_WAIT |
| TC-004 | Pairing Record Persistence        | T      | Pass    | 2026-03-28 | Pairing survives reboot; phone re-paired after clean wipe |
| TC-005 | Auto-Reconnect                    | D      | Pass    | 2026-03-28 | Phone auto-connects via BLE/RFCOMM after reboot, no user action needed |
| TC-006 | AP Configuration                  | T      | Partial | 2026-03-28 | NM AP on uap0 works; channel 48 (2.4 GHz) not matching config channel 149 (5 GHz) |
| TC-007 | DHCP Lease Assignment             | T      | Pass    | 2026-03-28 | Phone received IP on AP subnet |
| TC-008 | TCP Listen Port 5000              | T      | Pass    | 2026-03-28 | Verified via `ss -tlnH` during BT_PAIRING |
| TC-009 | TLS + Version + Service Discovery | T      | Pass    | 2026-03-28 | AA projection fully operational |
| TC-010 | Reconnection on Loss              | T      | Partial | 2026-03-28 | AA disconnect+reconnect works; WiFi toggle causes phone to rejoin house WiFi instead of AP |
| TC-011 | H.264 Decode & Display            | D      | Pending |            | Requires re-run against AndrewGraydon/openauto binary (GSTVideoOutput rewrite) |
| TC-012 | PipeWire Audio Path               | T      | Pass    | 2026-03-28 | Audio through PipeWire → BT A2DP speaker |
| TC-013 | Four Audio Streams                | D+I    | Pending |            |       |
| TC-014 | Audio Focus / Ducking             | D      | Pass    | 2026-03-28 | Nav voice ducks music, music resumes after |
| TC-015 | BT A2DP Audio Output              | D      | Pass    | 2026-03-28 | Audio on BT speaker, not HDMI |
| TC-016 | BT Audio Auto-Reconnect           | T      | Pending |            |       |
| TC-017 | Touch Event Path                  | T      | Pass    | 2026-03-28 | Single-tap working after libinput fix |
| TC-018 | Multi-Touch                       | T      | Fail    | 2026-03-28 | HW supports 6-point MT; OpenAuto sends single-touch only (upstream limit) |
| TC-019 | Ignition Sense Detection          | M      | Blocked |            | GPIO 17 not wired yet |
| TC-020 | Clean Shutdown                    | M      | Blocked |            | Requires ignition GPIO |
| TC-021 | Auto-Boot on Power                | T      | Pass    | 2026-03-28 | piauto.service enabled, starts on boot |
| TC-022 | Fan PWM Control                   | M      | Blocked |            | Fan not connected yet |
| TC-023 | Splash Screen                     | D      | Pass    | 2026-03-28 | Status text shown, transitions to AA |
| TC-024 | Night Mode Sensor                 | T      | Pending |            |       |
| TC-025 | Read-Only Root & /data            | I+T    | Pending |            |       |
| TC-026 | Boot Time                         | M      | Pass    | 2026-03-28 | BOOTING→IDLE in ~1s; systemd start to IDLE ~2s (req: <25s) |
| TC-027 | Projection Latency                | M      | Pending |            | Requires camera setup |
| TC-028 | Video Frame Rate                  | M      | Pending |            |       |
| TC-029 | Audio Latency                     | M      | Pending |            | Requires recording equipment |
| TC-030 | Connection Setup Time             | M      | Partial | 2026-03-28 | PhoneDetected→PROJECTION_ACTIVE 16s (req: <15s); WiFi join adds ~4s delay |
| TC-031 | Power-Loss Resilience             | T      | Pending |            |       |
| TC-032 | 12-Hour Soak Test                 | T      | Pending |            |       |
| TC-033 | Config & Logging Inspection       | I      | Pass    | 2026-03-28 | Single YAML config, journald only, no log files |
| TC-034 | BT Speaker Mid-Session Disconnect | T      | Pending |            |       |
| TC-035 | Clock Initialization (No RTC)     | T+I    | Pass    | 2026-03-28 | System time ahead of saved /data/clock after reboot; correct time restored |
| TC-036 | BT_PAIRING Timeout                | T      | Pending |            |       |
| TC-037 | TCP_CONNECT Timeout               | T      | Pending |            |       |
| TC-038 | Boot Timeout                      | T      | Pending |            |       |
| TC-039 | Missing/Corrupt Config File       | T      | Pass    | 2026-03-28 | Boots with defaults, logs warning, no crash |
| TC-040 | Phone Volume → PipeWire Sync      | T      | Pass    | 2026-03-28 | AVRCP 34/127→0.27, 43/127→0.34 mapped to wpctl |
| TC-041 | BT Speaker Pairing UI             | D      | Pass    | 2026-03-28 | Scan, pair, connect via touchscreen |
| TC-042 | Return to Splash on Disconnect    | T      | Pass    | 2026-03-28 | onAndroidAutoQuit detected, splash shown |
| TC-043 | Single-Tap (No Double-Tap)        | T      | Pass    | 2026-03-28 | libinput disabled, evdevtouch:grab |
| TC-044 | USB BT Dongle for Speaker         | T      | Pass    | 2026-03-28 | hci1 USB, no stuttering |
| TC-045 | AP+STA Dual Interface             | T      | Pass    | 2026-03-28 | uap0 AP (192.168.50.1) + wlan0 STA (10.10.0.190) simultaneous |
| TC-046 | aasdk Build (OpenSSL 3.x)         | I      | Pending |            | Pending Pi build |
| TC-047 | openauto Build (GSTVideoOutput)   | I      | Pending |            | Pending Pi build |
| TC-048 | GStreamer Pipeline Init           | T      | Pending |            | Pending Pi build |
| TC-049 | Audio Stutter Regression          | D      | Pending |            | Pending Pi build; verifies KI-001 fix |
| TC-050 | HW H.264 Decoder (v4l2h264dec)    | T      | Pending |            | Pending Pi build |
| TC-051 | VideoWidget Frame Rendering       | D      | Pending |            | Pending Pi build; verifies KI-002 fix |
| TC-052 | Full Projection — New Binary E2E  | D      | Pending |            | Pending Pi build |
| TC-053 | Software Fallback Decoder         | T      | Pending |            | Pending Pi build |

---

## 27. Known Issues

| ID | Summary | Severity | Root Cause | Status |
| :- | :------ | :------- | :--------- | :----- |
| KI-001 | Audio stutter when notifications or Gemini trigger during music playback | Medium | RtAudio race condition — three audio stream instances (media, guidance, system) concurrently access shared RtAudio buffers without synchronization. | Pending build verification — `AndrewGraydon/openauto` piauto-debian13 branch incorporates OpenDsh PR #32 static mutex fix. Verify with TC-049 after Pi build. |
| KI-002 | Video sizing and touch input broken with QtVideoOutput path on EGLFS | High | `QtVideoOutput` (QMediaPlayer + QVideoWidget) cannot render raw H.264 NAL units on EGLFS. | Pending build verification — `GSTVideoOutput` fully rewritten to use plain GStreamer C API in `AndrewGraydon/openauto`. Verify with TC-011 and TC-052 after Pi build. |
| KI-003 | Phone occasionally reconnects to house WiFi instead of PiAuto AP | Low | After WiFi toggle or extended idle, phone's WiFi auto-join prioritizes known networks over PiAuto AP. Usually resolves after BT disconnect/reconnect cycle triggers fresh credential exchange. | Intermittent — workaround is BT reconnect cycle. |
| KI-004 | Connection setup time slightly exceeds 15s target | Low | PhoneDetected → PROJECTION_ACTIVE measured at 16s. WiFi join adds ~4s delay when phone must switch from house WiFi to PiAuto AP. | TC-030 partial pass. |
