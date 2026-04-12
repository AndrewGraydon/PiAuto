# Software Requirements Specification: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-SRS-001               |
| Version        | 3.3                          |
| Date           | 2026-04-11                   |
| Status         | Active                       |

## 1. Introduction

### 1.1 Purpose

This document defines the software requirements for PiAuto, a standalone Wireless Android Auto (WAA) head unit built on the Raspberry Pi 4 Model B. It serves as the authoritative source for all functional, performance, and non-functional requirements against which the system shall be designed, implemented, and verified.

### 1.2 Scope

PiAuto is a prototype automotive head unit that wirelessly mirrors an Android phone's Android Auto interface onto a 7-inch 1024×600 touchscreen display. The system handles Bluetooth Low Energy discovery, Wi-Fi access point management, Android Auto protocol negotiation (via OpenAuto / aasdk), H.264 video decoding, concurrent audio streaming over Bluetooth A2DP to a vehicle speaker, and capacitive touch input.

**In scope:**

- Wireless Android Auto projection (WAA)
- Bluetooth A2DP audio output to vehicle speaker
- Multi-phone pairing with auto-reconnect
- Ignition-sense power management
- Thermal management (fan PWM)
- Read-only root filesystem for reliability
- Day/night mode support (phone-controlled)
- OBD-II via Bluetooth (future/secondary)
- On-screen setup UI for BT speaker pairing, WiFi config, and display settings (future/secondary)

**Out of scope:**

- Wired (USB) Android Auto
- Apple CarPlay (requires Apple MFi hardware authentication IC — not feasible for this project)
- FM/AM radio, AUX input, or other source switching
- CAN bus direct integration
- Over-the-air (OTA) updates
- Voice assistant / microphone input ("OK Google")
- Physical button / steering wheel control input
- Instrument cluster integration (NavigationStatus, MediaPlaybackStatus services)

### 1.3 Definitions and Acronyms

| Term       | Definition                                                         |
| :--------- | :----------------------------------------------------------------- |
| WAA        | Wireless Android Auto                                              |
| AA         | Android Auto                                                       |
| AAP        | Android Auto Protocol                                              |
| AOAP       | Android Open Accessory Protocol                                    |
| AP         | Access Point (Wi-Fi)                                               |
| BLE        | Bluetooth Low Energy                                               |
| BT         | Bluetooth                                                          |
| A2DP       | Advanced Audio Distribution Profile (BT stereo audio)              |
| HFP        | Hands-Free Profile (BT telephony audio)                            |
| aasdk      | Android Auto SDK — open-source C++ library implementing the AA head unit protocol |
| OpenAuto   | Open-source AA head unit emulator built on aasdk and Qt            |
| DFS        | Dynamic Frequency Selection (5 GHz radar avoidance)                |
| EGLFS      | Qt platform plugin for direct KMS/DRM rendering without X11        |
| KMS/DRM    | Kernel Mode Setting / Direct Rendering Manager                     |
| OBD-II     | On-Board Diagnostics, second generation                            |
| PWM        | Pulse Width Modulation                                             |
| PipeWire   | Modern Linux multimedia framework replacing PulseAudio and JACK    |
| V4L2       | Video4Linux2 — Linux kernel video capture/decode API               |
| overlayfs  | Union filesystem that layers a writable tmpfs over a read-only root|
| HUIG       | Head Unit Integration Guide (Google's AA specification, v1.3+)     |

### 1.4 References

| ID             | Document                              |
| :------------- | :------------------------------------ |
| PiAuto-ARCH-001| Architecture Document                 |
| PiAuto-ICD-001 | Interface Control Document            |
| PiAuto-SM-001  | State Machine Specification           |
| PiAuto-HW-001  | Hardware & Power Specification        |
| PiAuto-RTM-001 | Requirements Traceability Matrix      |
| PiAuto-TP-001  | Test Plan                             |
| PiAuto-IG-001  | Implementation Guide                  |
| HUIG v1.3      | Google Head Unit Integration Guide    |

---

## 2. System Overview

PiAuto replaces a vehicle's factory head unit (or supplements it) with a Raspberry Pi 4B driving a 7-inch HDMI touchscreen. An Android phone discovers the Pi via Bluetooth Low Energy, receives Wi-Fi AP credentials, joins the Pi's 5 GHz access point, and establishes an encrypted TCP tunnel. The phone then streams H.264 video and audio to the Pi, which decodes and renders them in real time via OpenAuto (built on the aasdk library). Touch input on the Pi's screen is serialized back to the phone as normalized coordinate events.

Audio is routed from the Pi over Bluetooth A2DP to a vehicle speaker (or speaker system). The system boots when the vehicle ignition is turned on and shuts down cleanly when ignition is turned off.

---

## 3. Functional Requirements

### 3.1 Bluetooth & Discovery

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-001 | The system shall advertise the WAA service UUID `9b3f6c10-a4d2-418e-a2b9-0700300de8f4` over Bluetooth Low Energy (BLE) for phone discovery. | Must |
| FR-002 | The system shall register a BlueZ RFCOMM Profile1 (UUID `4de17a00-52cb-11e6-bdf4-0800200c9a66`, channel 8) and accept incoming connections from phones for WAA credential exchange. | Must |
| FR-003 | Upon RFCOMM connection, the system shall exchange Protobuf messages (WifiInfoResponse, WifiStartResponse) containing the AP SSID, BSSID, password, security mode, static IP, and TCP port. | Must |
| FR-004 | The system shall store pairing records for up to 8 phones in persistent storage.                             | Must     |
| FR-005 | On entering IDLE state, the system shall attempt to reconnect to the most recently connected phone before advertising to new devices. | Should |

### 3.2 Wi-Fi Access Point

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-006 | The system shall start a 5 GHz 802.11ac (Wi-Fi 5) access point upon successful credential exchange. In AP+STA mode the AP is managed by NetworkManager (profile `piauto-ap` on `uap0`); in standalone mode, hostapd is used on `wlan0`. | Must     |
| FR-007 | The AP shall operate on channel 149 or 165 to avoid DFS/TPC regulatory restrictions.                        | Must     |
| FR-008 | The AP shall use WPA2-AES encryption with a minimum 8-character PSK.                                        | Must     |
| FR-009 | The AP shall assign the Pi a static IP on the AP interface: `192.168.50.1` in AP+STA mode (uap0, NM-managed) or `192.168.1.1` in standalone mode (wlan0, hostapd). | Must     |
| FR-010 | The AP shall provide DHCP to connected clients with mode-appropriate address ranges.                        | Must     |

### 3.3 Connection & Projection Tunnel

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-011 | The system shall listen on TCP port 5000 for incoming AA projection connections.                             | Must     |
| FR-012 | The system shall complete a TLS 1.2+ handshake before accepting projection data.                             | Must     |
| FR-013 | The system shall perform AA version negotiation (MESSAGE_VERSION_REQUEST / MESSAGE_VERSION_RESPONSE) on the Control channel before starting services. | Must |
| FR-014 | The system shall perform AA service discovery, announcing supported services (Media Sink, Input Source, Sensor Source) and negotiating active channels with the phone. | Must |
| FR-015 | On connection loss, the system shall attempt reconnection up to 3 times with a 5-second interval before returning to IDLE. | Should |

### 3.4 Video

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-016 | The system shall decode incoming H.264 Baseline Profile video using the Pi 4's hardware V4L2 decoder.       | Must     |
| FR-017 | Decoded video shall be rendered to the display via Qt EGLFS (direct KMS/DRM, no X11 or Wayland compositor). | Must     |
| FR-018 | The video pipeline shall support the AA stream resolution of 800×480 (480p mode) at 30 FPS. Note: 800×480 is the Android Auto protocol stream resolution, not the physical display resolution. The physical display is 1024×600 native; the VideoWidget scales the 800×480 AA stream to fill the display. | Must     |
| FR-019 | The system shall negotiate 800×480 resolution and 30 FPS with the phone during AA service discovery, with a maximum bitrate of 4,000 kbps. | Must |

### 3.5 Audio

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-020 | The system shall use PipeWire as the audio subsystem to handle concurrent audio streams.                     | Must     |
| FR-021 | The system shall accept four concurrent AA audio streams as defined by the AA protocol: Media, Guidance, System Audio, and Telephony. | Must |
| FR-022 | The Media stream shall be accepted as PCM 16-bit 48,000 Hz stereo or AAC-LC.                                | Must     |
| FR-023 | The Guidance, System Audio, and Telephony streams shall be accepted as PCM 16-bit 16,000 Hz mono.           | Must     |
| FR-024 | Audio focus management shall follow the AA protocol (GAIN, GAIN_TRANSIENT, GAIN_TRANSIENT_MAY_DUCK, RELEASE). Media shall be ducked during Guidance and muted during Telephony. | Must |
| FR-025 | All audio output shall be routed over Bluetooth A2DP to the paired vehicle speaker.                          | Must     |
| FR-026 | The system shall automatically reconnect to the last known A2DP audio sink on boot.                          | Should   |

### 3.6 Touch Input

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-027 | The system shall read touch events from the USB HID capacitive touchscreen.                                  | Must     |
| FR-028 | Touch coordinates shall be normalized to a 0–10,000 range on both X and Y axes, regardless of physical resolution. | Must |
| FR-029 | Normalized touch events shall be serialized as AA Input messages and transmitted via the Input Source service to the phone. | Must |
| FR-030 | Touch events shall follow the AA touch protocol: ACTION_DOWN, ACTION_UP, ACTION_MOVED, ACTION_POINTER_DOWN, ACTION_POINTER_UP, with pointer ID tracking. | Must |
| FR-031 | The system shall support multi-touch (up to 5 simultaneous points).                                          | Should   |

### 3.7 Power Management

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-032 | The system shall monitor GPIO 17 (ignition sense) for a LOW signal indicating ignition OFF.                  | Must     |
| FR-033 | Upon detecting ignition OFF, the system shall execute a clean shutdown sequence (`shutdown -h now`) within 10 seconds. | Must |
| FR-034 | The system shall boot automatically when 5.1 V power is applied (ignition ON).                               | Must     |

### 3.8 Thermal Management

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-035 | The system shall monitor CPU temperature and control a PWM fan on GPIO 4 using the following profile: OFF below 50 °C, 50 % duty above 50 °C, 100 % duty above 65 °C. | Must |

### 3.9 User Interface

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-036 | When no phone is connected (IDLE state), the system shall display a static splash screen indicating "Waiting for phone." | Must |
| FR-037 | When a connection is in progress (BT_PAIRING through TCP_CONNECT states), the splash screen shall display the current connection status. | Should |

### 3.10 Day/Night Mode

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-038 | The system shall expose the AA Sensor Source service with SENSOR_NIGHT_MODE support.                         | Should   |
| FR-039 | Night mode shall default to phone-controlled (the phone determines day/night based on its own sensors).      | Should   |

### 3.11 Audio Output Resilience

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-040 | If the BT A2DP speaker disconnects during projection, audio shall be routed to a null sink (silent). PipeWire shall attempt reconnection to the speaker automatically. | Should |
| FR-041 | The system shall not halt or crash if no audio output sink is available.                                      | Must     |

### 3.12 System Time

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-042 | On boot, the system shall set the system clock from the build timestamp or last known time stored in `/data/clock` to ensure monotonically increasing time. | Should |
| FR-043 | TLS certificate validation shall use a self-signed certificate that does not depend on accurate wall-clock time. | Must |

### 3.13 Filesystem & Storage

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-044 | The root filesystem shall be mounted read-only using overlayfs, with a writable tmpfs overlay for runtime state. | Must |
| FR-045 | Persistent data (pairing records, configuration) shall be stored on a separate writable partition.            | Must     |

### 3.14 OBD-II Integration (Future)

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-046 | The system should support Bluetooth SPP or BLE connection to an ELM327-compatible OBD-II adapter.            | Could    |
| FR-047 | OBD-II data (vehicle speed, RPM, coolant temperature) should be readable via standard PIDs.                  | Could    |

### 3.15 On-Screen Setup UI (Future)

| ID     | Requirement                                                                                                  | Priority |
| :----- | :----------------------------------------------------------------------------------------------------------- | :------- |
| FR-048 | The system should display an on-screen setup interface during IDLE state for initial device configuration.    | Could    |
| FR-049 | The setup UI should allow Bluetooth audio speaker discovery, pairing, and selection via the touchscreen.      | Could    |
| FR-050 | The setup UI should allow editing WiFi AP settings (SSID, password, channel, country code).                   | Could    |
| FR-051 | The setup UI should display system status (CPU temp, BLE state, paired devices, IP address).                  | Could    |

---

## 4. Performance Requirements

| ID     | Requirement                                                                                                  | Metric            |
| :----- | :----------------------------------------------------------------------------------------------------------- | :----------------- |
| PR-001 | Total boot-to-IDLE time (power applied to splash screen displayed) shall be less than 25 seconds.            | < 25 s             |
| PR-002 | End-to-end projection latency (phone render to Pi display) shall not exceed 200 ms.                          | ≤ 200 ms           |
| PR-003 | Video decoding shall sustain 30 FPS (the maximum supported by the AA protocol at the 800×480 stream resolution). | 30 FPS             |
| PR-004 | Audio output latency (AA stream to BT speaker) shall not exceed 100 ms.                                      | ≤ 100 ms           |
| PR-005 | Time from BT detection to PROJECTION_ACTIVE shall not exceed 15 seconds for a previously paired phone.       | ≤ 15 s             |

---

## 5. Non-Functional Requirements

### 5.1 Reliability

| ID     | Requirement                                                                                                  |
| :----- | :----------------------------------------------------------------------------------------------------------- |
| NR-001 | The system shall recover from a transient phone disconnection without requiring a full reboot.               |
| NR-002 | The system shall not corrupt persistent data (pairing records) on unclean power loss, ensured by the read-only root filesystem. |
| NR-003 | The system shall operate continuously for the duration of a typical driving session (up to 12 hours) without requiring a restart. |

### 5.2 Usability

| ID     | Requirement                                                                                                  |
| :----- | :----------------------------------------------------------------------------------------------------------- |
| NR-004 | The system shall require zero user interaction to connect to a previously paired phone — connection shall be fully automatic. |
| NR-005 | First-time phone pairing shall require only standard Android Bluetooth pairing (no custom app or manual configuration on the Pi). |

### 5.3 Maintainability

| ID     | Requirement                                                                                                  |
| :----- | :----------------------------------------------------------------------------------------------------------- |
| NR-006 | All configuration (SSID, password, fan thresholds) shall be defined in a single YAML configuration file on the writable partition. |
| NR-007 | The system shall log operational events and errors to a journald ring buffer (volatile, not written to SD card). |
| NR-008 | The system shall detect a Bluetooth daemon (`bluetoothd`) crash and automatically restart itself via systemd within 5 seconds so that all Bluetooth profile registrations are restored without user intervention. |

---

## 6. Constraints

| ID   | Constraint                                                                                  |
| :--- | :------------------------------------------------------------------------------------------ |
| C-001| Target hardware is Raspberry Pi 4 Model B (4 GB RAM).                                      |
| C-002| Display is a 7-inch HDMI display with USB capacitive touch (LCDWiki 7inch HDMI Display-B). The physical display native resolution is 1024×600. The Android Auto protocol streams video at 800×480 (480p mode); the VideoWidget scales this to fill the display. |
| C-003| Operating system shall be Raspberry Pi OS Lite (Trixie, 64-bit) with no desktop environment. |
| C-004| The Android Auto protocol shall be handled by OpenAuto built on the aasdk library.          |
| C-005| OpenAuto shall render via Qt EGLFS (direct KMS/DRM) — no X11 or Wayland compositor.        |
| C-006| Orchestration and application code shall be written in Python 3.11+.                        |
| C-007| Audio subsystem shall be PipeWire.                                                          |
| C-008| The Android Auto video stream resolution is limited to 800×480 at 30 FPS maximum (AA protocol 480p mode constraint). The physical display renders at 1024×600 native resolution; the VideoWidget scales the 800×480 AA stream to fill the display. |

---

## 7. Assumptions

| ID   | Assumption                                                                                  |
| :--- | :------------------------------------------------------------------------------------------ |
| A-001| The Android phone runs Android 11 or later with Wireless Android Auto support.              |
| A-002| The vehicle provides a stable 12 V DC supply (nominal) with a buck converter to 5.1 V.     |
| A-003| A Bluetooth speaker or audio receiver is available within BT range for A2DP audio output.   |
| A-004| The 5 GHz radio band is available in the operating region (not all countries permit all channels). |
| A-005| The phone supports BLE for WAA discovery (standard on Android 11+).                         |
