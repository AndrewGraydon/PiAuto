# Requirements Traceability Matrix: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-RTM-001               |
| Version        | 3.3                          |
| Date           | 2026-04-11                   |
| Status         | Active                       |

## 1. Introduction

### 1.1 Purpose

This document traces every requirement in PiAuto-SRS-001 forward to the architecture component that implements it, the interface that defines its data exchange, the state machine state(s) where it is active, and the test case that verifies it.

### 1.2 How to Read This Matrix

- **Forward trace (requirement → design → test):** Every row starts with an SRS requirement ID and links it to architecture, ICD, state machine, and test plan.
- **Backward trace (design → requirement):** §5 lists architectural components and confirms each has at least one requirement driving it.
- A cell marked "—" means no direct mapping to that column.

---

## 2. Functional Requirements Trace

| SRS ID | Requirement Summary            | Architecture (ARCH §) | ICD Interface | State Machine State(s) | Test Case |
| :----- | :----------------------------- | :--------------------- | :------------ | :---------------------- | :-------- |
| FR-001 | Advertise WAA BLE service UUID | BlueZ (§4.2)          | IF-01 (§3)    | IDLE                    | TC-001    |
| FR-002 | BLE WAA handshake              | BlueZ (§4.2)          | IF-01 (§3)    | BT_PAIRING              | TC-002    |
| FR-003 | Send WifiStartRequest Protobuf | BlueZ (§4.2)          | IF-01 (§3.5)  | BT_PAIRING              | TC-003    |
| FR-004 | Store pairing records (up to 8)| Config Manager (§4.1)  | —             | BT_PAIRING (exit)       | TC-004    |
| FR-005 | Auto-reconnect to last phone   | BlueZ + State Machine (§4.1, §4.2) | IF-01 (§3.6) | IDLE | TC-005 |
| FR-006 | Start 5 GHz 802.11ac AP       | hostapd (§4.2)         | IF-02 (§4)    | WIFI_WAIT               | TC-006    |
| FR-007 | AP on channel 149/165 (no DFS) | hostapd (§4.2)         | IF-02 (§4.2)  | WIFI_WAIT               | TC-006    |
| FR-008 | WPA2-AES with min 8-char PSK  | hostapd (§4.2)         | IF-02 (§4.2)  | WIFI_WAIT               | TC-006    |
| FR-009 | Pi static IP on AP interface  | NetworkManager / hostapd (§4.2) | IF-02 (§4.3) | WIFI_WAIT | TC-006 |
| FR-010 | DHCP on AP interface          | NetworkManager / dnsmasq (§4.2) | IF-02 (§4.3) | WIFI_WAIT | TC-007 |
| FR-011 | Listen on TCP port 5000       | OpenAuto (§4.3)        | IF-03 (§5)    | TCP_CONNECT             | TC-008    |
| FR-012 | TLS 1.2+ handshake            | OpenAuto (§4.3)        | IF-03 (§5.2)  | TCP_CONNECT             | TC-009    |
| FR-013 | AA version negotiation         | OpenAuto (§4.3)        | IF-03 (§5.6)  | TCP_CONNECT             | TC-009    |
| FR-014 | AA service discovery           | OpenAuto (§4.3)        | IF-04 (§6)    | TCP_CONNECT             | TC-009    |
| FR-015 | Reconnect up to 3 times       | State Machine (§4.1)   | IF-03 (§5.7)  | ERROR_RECOVERY          | TC-010    |
| FR-016 | H.264 BP decode via V4L2 HW   | V4L2 Decoder (§4.4)   | IF-05 (§7)    | PROJECTION_ACTIVE       | TC-011    |
| FR-017 | Render via Qt EGLFS (no X11)  | Qt EGLFS (§4.4)       | IF-05 (§7)    | PROJECTION_ACTIVE       | TC-011    |
| FR-018 | 800×480 at 30 FPS             | Qt EGLFS + V4L2 (§4.4)| IF-05 (§7.2)  | PROJECTION_ACTIVE       | TC-011    |
| FR-019 | Negotiate 800×480/30fps/4Mbps | OpenAuto (§4.3)        | IF-05 (§7.2)  | TCP_CONNECT             | TC-011    |
| FR-020 | Use PipeWire for audio        | PipeWire (§4.4)        | IF-06 (§8)    | PROJECTION_ACTIVE       | TC-012    |
| FR-021 | Accept 4 audio streams        | OpenAuto + PipeWire (§4.3, §4.4) | IF-06 (§8.2) | PROJECTION_ACTIVE | TC-013 |
| FR-022 | Media: 48kHz stereo PCM/AAC   | OpenAuto (§4.3)        | IF-06 (§8.2)  | PROJECTION_ACTIVE       | TC-013    |
| FR-023 | Guidance/System/Tel: 16kHz mono| OpenAuto (§4.3)       | IF-06 (§8.2)  | PROJECTION_ACTIVE       | TC-013    |
| FR-024 | AA audio focus management      | OpenAuto (§4.3)        | IF-06 (§8.3)  | PROJECTION_ACTIVE       | TC-014    |
| FR-025 | Route audio over BT A2DP      | PipeWire + BlueZ (§4.2, §4.4) | IF-08 (§10) | PROJECTION_ACTIVE | TC-015 |
| FR-026 | Auto-reconnect BT audio sink  | WirePlumber (§4.4)     | IF-08 (§10)   | BOOTING, IDLE           | TC-016    |
| FR-027 | Read USB HID touch events     | Qt EGLFS evdev (§4.5)  | IF-07 (§9)    | PROJECTION_ACTIVE       | TC-017    |
| FR-028 | Normalize touch to 0–10,000   | OpenAuto (§4.3, §4.5)  | IF-07 (§9.4)  | PROJECTION_ACTIVE       | TC-017    |
| FR-029 | Serialize touch as AA Input    | OpenAuto (§4.3)        | IF-07 (§9.3)  | PROJECTION_ACTIVE       | TC-017    |
| FR-030 | AA touch protocol actions      | OpenAuto (§4.3)        | IF-07 (§9.3)  | PROJECTION_ACTIVE       | TC-017    |
| FR-031 | 5-point multi-touch           | Qt EGLFS + OpenAuto    | IF-07 (§9.2)  | PROJECTION_ACTIVE       | TC-018    |
| FR-032 | Monitor GPIO 17 for ignition  | GPIO Manager (§4.5)    | IF-09 (§11)   | All states              | TC-019    |
| FR-033 | Clean shutdown within 10 s    | GPIO Manager + SM (§4.1, §4.5) | IF-09 (§11.2) | SHUTDOWN | TC-020 |
| FR-034 | Auto-boot on power            | Hardware (boot)         | —             | BOOTING                 | TC-021    |
| FR-035 | PWM fan control (GPIO 4)      | Thermal Monitor + GPIO Manager (§4.5) | IF-10 (§12) | All states | TC-022 |
| FR-036 | Splash screen in IDLE         | Qt EGLFS splash app (§4.4) | —          | IDLE                    | TC-023    |
| FR-037 | Status display during connection | Qt EGLFS splash app (§4.4) | —         | BT_PAIRING–TCP_CONNECT  | TC-023    |
| FR-038 | Sensor Source with night mode  | OpenAuto (§4.3)        | IF-04 (§6.4)  | PROJECTION_ACTIVE       | TC-024    |
| FR-039 | Night mode phone-controlled   | OpenAuto (§4.3)        | IF-04 (§6.4)  | PROJECTION_ACTIVE       | TC-024    |
| FR-040 | BT speaker disconnect → null sink | PipeWire + WirePlumber (§4.4) | IF-08 (§10) | PROJECTION_ACTIVE | TC-034 |
| FR-041 | No crash if no audio sink     | PipeWire (§4.4)        | IF-08 (§10)   | All states              | TC-034    |
| FR-042 | Set clock from /data/clock    | Clock Module (§4.1)    | —             | BOOTING                 | TC-035    |
| FR-043 | TLS cert independent of clock | OpenAuto (§4.3)        | IF-03 (§5.2)  | TCP_CONNECT             | TC-035    |
| FR-044 | Read-only root with overlayfs | Deployment (§7.1)      | —             | All states              | TC-025    |
| FR-045 | Writable /data partition       | Deployment (§7.2)      | —             | All states              | TC-025    |
| FR-046 | OBD-II BT connection (future) | BlueZ (§4.2)           | —             | TBD                     | TBD       |
| FR-047 | Read OBD-II PIDs (future)     | —                       | —             | TBD                     | TBD       |
| FR-048 | On-screen setup UI (future)   | Splash/Qt EGLFS (§4.6) | —             | IDLE                    | TBD       |
| FR-049 | BT speaker pairing UI (future)| BlueZ + Qt (§4.2, §4.6)| —             | IDLE                    | TBD       |
| FR-050 | WiFi config UI (future)       | Qt EGLFS (§4.6)        | —             | IDLE                    | TBD       |
| FR-051 | System status UI (future)     | Qt EGLFS (§4.6)        | —             | IDLE                    | TBD       |

---

## 3. Performance Requirements Trace

| SRS ID | Requirement Summary            | Verification Method   | Architecture Component       | Test Case |
| :----- | :----------------------------- | :-------------------- | :--------------------------- | :-------- |
| PR-001 | Boot-to-IDLE < 25 s           | Measurement           | Boot sequence, systemd       | TC-026    |
| PR-002 | Projection latency ≤ 200 ms   | Measurement           | Full pipeline                | TC-027    |
| PR-003 | Video 30 FPS sustained        | Measurement           | V4L2 + Qt EGLFS             | TC-028    |
| PR-004 | Audio latency ≤ 100 ms        | Measurement           | PipeWire + BT A2DP          | TC-029    |
| PR-005 | BT detect → Projection ≤ 15 s | Measurement           | Full connection pipeline     | TC-030    |

---

## 4. Non-Functional Requirements Trace

| SRS ID | Requirement Summary            | Architecture Component       | Verification Method | Test Case |
| :----- | :----------------------------- | :--------------------------- | :------------------ | :-------- |
| NR-001 | Recover from transient disconnect | State Machine (ERROR_RECOVERY) | Test           | TC-010    |
| NR-002 | No data corruption on power loss | overlayfs (read-only root)  | Test              | TC-031    |
| NR-003 | 12-hour continuous operation   | All                          | Soak test          | TC-032    |
| NR-004 | Zero-interaction auto-connect  | BlueZ + State Machine        | Demonstration      | TC-005    |
| NR-005 | Standard Android BT pairing   | BlueZ                        | Demonstration      | TC-002    |
| NR-006 | Single YAML config file        | Config Manager               | Inspection         | TC-033    |
| NR-007 | journald ring buffer logging   | Logger (systemd-journald)    | Inspection         | TC-033    |
| NR-008 | Self-heal on BlueZ daemon crash | State Machine + BleManager (watch_bluez_restart) | Test | TC-058 |

---

## 5. Backward Trace (Component → Requirement)

| Architecture Component   | Driving Requirements                    | Justified |
| :----------------------- | :-------------------------------------- | :-------- |
| State Machine (Python)   | All FR-*, NR-001, NR-004, NR-008      | Yes       |
| Config Manager           | FR-004, NR-006                          | Yes       |
| Logger (journald)        | NR-007                                  | Yes       |
| BlueZ (BLE + Classic)    | FR-001–005, FR-025, FR-026             | Yes       |
| hostapd                  | FR-006–009                              | Yes       |
| dnsmasq                  | FR-010                                  | Yes       |
| OpenAuto (aasdk)         | FR-011–014, FR-019, FR-021–024, FR-028–030, FR-038, FR-039 | Yes |
| GStreamer Decoder (v4l2h264dec/avdec_h264) | FR-016, PR-003             | Yes       |
| Qt 5 EGLFS               | FR-017, FR-036, FR-037                 | Yes       |
| PipeWire + WirePlumber   | FR-020, FR-025, FR-026, FR-040, FR-041, PR-004 | Yes |
| Touch Input (Qt evdev)   | FR-027, FR-031                          | Yes       |
| GPIO Manager (libgpiod)  | FR-032–035                              | Yes       |
| Thermal Monitor          | FR-035                                  | Yes       |
| Clock Module             | FR-042, FR-043                          | Yes       |

**Orphan check:** No orphan components. All components trace to at least one requirement.

---

## 6. Coverage Summary

| Category           | Total Requirements | Traced to Architecture | Traced to Test | Coverage |
| :----------------- | :----------------- | :--------------------- | :------------- | :------- |
| Functional (FR)    | 51                 | 45 (6 future TBD)     | 45             | 88 %     |
| Performance (PR)   | 5                  | 5                      | 5              | 100 %    |
| Non-Functional (NR)| 7                  | 7                      | 7              | 100 %    |
| **Total**          | **63**             | **57**                 | **57**         | **90 %** |

The 6 untraced requirements are explicitly marked as future/secondary scope: OBD-II integration (FR-046, FR-047) and on-screen setup UI (FR-048–FR-051).
