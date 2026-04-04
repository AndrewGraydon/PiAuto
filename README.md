# PiAuto

Wireless Android Auto head unit built on Raspberry Pi 4.

PiAuto turns a Raspberry Pi 4B and a 7-inch touchscreen into a standalone Wireless Android Auto head unit. Your phone discovers the Pi over Bluetooth Low Energy, joins its 5 GHz Wi-Fi access point, and streams Android Auto to the display — no cables, no phone mount.

## How It Works

```
Phone ──BLE──► Pi (discover + pair, first time only)
Phone ──BT HFP──► Pi (subsequent boots — triggers Android car mode)
Phone ──WiFi──► Pi AP (5 GHz 802.11ac)
Phone ──TCP/TLS──► OpenAuto (AA projection on port 5000)
Pi ──BT A2DP──► Vehicle Speaker (audio output)
```

**First pairing:**
1. Pi advertises a Wireless Android Auto BLE service for discovery
2. Phone pairs over Classic BT and receives Wi-Fi credentials via RFCOMM
3. Phone joins the Pi's 5 GHz access point
4. OpenAuto handles the AA session (video, audio, touch)
5. Audio routes over Bluetooth A2DP to your vehicle speaker

**Subsequent boots (auto-reconnect):**
1. Pi pages the last known phone via Classic BT and registers an HFP Hands-Free profile
2. Android detects the HFP device and enters car mode — Android Auto initiates RFCOMM automatically
3. Phone joins the Pi's AP and projection begins within seconds, no user interaction required

## Features

- **Wireless Android Auto** — no USB cable required
- **Auto-reconnect** — HFP Hands-Free profile triggers Android's car mode on boot; AA launches in <2 s, retries every 30 s until phone is in range; no user interaction required
- **Fast disconnect detection** — return to splash within ~3 s of phone ending AA session via four independent signals (TCP session end, RFCOMM retry, WiFi AP leave, BlueZ BT disconnect)
- **Multi-phone pairing** — stores up to 8 paired devices
- **Touchscreen BT speaker setup** — on-screen UI for discovering and pairing Bluetooth speakers
- **WiFi AP+STA** — simultaneous access point (for phone) and station (for SSH/internet) on one radio
- **Hardware video decode** — H.264 via GStreamer (`v4l2h264dec` on VideoCore VI, software fallback)
- **Compositorless rendering** — Qt EGLFS direct to display (no X11/Wayland)
- **Bluetooth audio output** — A2DP to vehicle speaker with 4 concurrent AA streams
- **Ignition-sense power management** — clean boot/shutdown with vehicle ignition
- **Thermal management** — PWM fan control with temperature-based profile
- **Power-loss resilient ext4** — journaling filesystem + BlueZ bind mount to `/data/` provides resilience without overlayfs
- **Power-loss resilience** — clock saved every 5 min during projection (≤5 min stale on power cut); BlueZ pairing DB persisted to `/data/bluetooth/` via bind mount so phone/speaker survive unexpected power loss without re-pairing
- **Graceful degradation** — develops and runs on non-Pi machines in mock mode

## Hardware

| Component | Specification |
| :-------- | :------------ |
| Board | Raspberry Pi 4 Model B (4 GB) |
| Display | 7" HDMI Display (1024×600 native, USB capacitive touch — wch.cn USB2IIC_CTP_CONTROL) |
| OS | Raspberry Pi OS Lite, Trixie, 64-bit |
| Audio | Bluetooth A2DP to vehicle speaker |
| Power | 5.1 V via vehicle buck converter, GPIO 17 ignition sense |
| Fan | PWM on GPIO 4 (off < 50 C, 50% at 50-65 C, 100% > 65 C) |

## Architecture

```
┌─────────────────────────────────────────────┐
│  Python Orchestrator (piauto)               │
│  ┌──────────────────────────────────────┐   │
│  │  State Machine                       │   │
│  │  BOOTING → IDLE → BT_PAIRING →      │   │
│  │  WIFI_WAIT → TCP_CONNECT →           │   │
│  │  PROJECTION_ACTIVE                   │   │
│  └──────────┬───────────────────────────┘   │
│             │ manages                        │
│  ┌──────────┼───────────────────────────┐   │
│  │  BLE    WiFi   GPIO   Thermal  Clock │   │
│  └──────────┼───────────────────────────┘   │
├─────────────┼───────────────────────────────┤
│  OpenAuto (C++) — AA protocol, video, audio │
│  Qt EGLFS │ V4L2 H.264 │ PipeWire │ Touch  │
└─────────────────────────────────────────────┘
```

The Python state machine orchestrates the connection lifecycle (BLE discovery, Wi-Fi AP, handoff). OpenAuto handles the entire Android Auto session internally.

## Package Structure

```
piauto/
├── __main__.py       # Entry point: python -m piauto
├── statemachine.py   # Central orchestrator (8 states)
├── ble.py            # BLE WAA advertising, RFCOMM credential exchange, HFP HF reconnect
├── bt_pair.py        # BR/EDR speaker discovery and pairing (dbus-next)
├── wifi.py           # WiFi AP management (NM-managed AP+STA or hostapd+dnsmasq standalone)
├── gpio.py           # Ignition sense + fan PWM (libgpiod)
├── thermal.py        # CPU temperature monitoring
├── config.py         # YAML config loader + validator
├── openauto.py       # OpenAuto process lifecycle
├── splash.py         # PyQt5 EGLFS splash + BT setup UI
├── volume.py         # AVRCP → PipeWire volume sync
├── clock.py          # System time save/restore (no RTC)
└── log.py            # journald / stderr logging
```

## Quick Start

### On a Raspberry Pi

See the full [Pi Setup Guide](docs/PiSetup.md) for hardware wiring, OS configuration, and service setup.

```bash
# 1. Clone with submodules (includes aasdk and openauto source)
git clone --recurse-submodules https://github.com/AndrewGraydon/PiAuto.git
cd PiAuto

# 2. Build and install OpenAuto + aasdk (~20 min on Pi 4B)
bash scripts/build-openauto.sh

# 3. Install runtime packages
sudo apt install -y bluez hostapd dnsmasq pipewire wireplumber \
    libqt5multimedia5-plugins gstreamer1.0-libav gstreamer1.0-plugins-bad \
    python3-pip python3-pyqt5

# 4. Install PiAuto
sudo pip3 install --break-system-packages .

# 5. Create config
sudo mkdir -p /data
sudo tee /data/piauto.yaml << 'EOF'
wifi:
  ssid: "PiAuto"
  password: "yourpassword"   # required — no default, min 8 characters
  channel: 149
  country: "AU"              # change to your country code
bluetooth:
  device_name: "PiAuto"
openauto:
  binary: "/usr/local/bin/autoapp"
EOF

# 6. Run
sudo python3 -m piauto
```

### On a Development Machine

The package runs in mock mode without Pi hardware — GPIO, BLE, and display functions become no-ops:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Config from a local file
export PIAUTO_CONFIG_PATH=./piauto.yaml
python -m piauto
```

## Configuration

All settings live in a single YAML file (default: `/data/piauto.yaml`):

```yaml
wifi:
  ssid: "PiAuto"
  password: "your-password-here"  # min 8 characters
  channel: 149                     # 149 or 165 (5 GHz, no DFS)
  country: "AU"                    # ISO 3166-1 alpha-2

bluetooth:
  device_name: "PiAuto"
  max_paired: 8

thermal:
  fan_low_temp: 50    # fan starts at 50% duty
  fan_high_temp: 65   # fan goes to 100% duty
  hysteresis: 3       # prevents rapid cycling

power:
  ignition_debounce_ms: 500
  shutdown_timeout_s: 10
```

If the config file is missing or invalid, PiAuto logs a warning and boots with built-in defaults.

## Documentation

The project follows specification-driven development. All documents are in [`docs/`](docs/):

| Document | Description |
| :------- | :---------- |
| [SRS](docs/SRS.md) | Software Requirements Specification (47 FR + 5 PR + 7 NR) |
| [Architecture](docs/Architecture.md) | System architecture and technology decisions |
| [ICD](docs/ICD.md) | Interface Control Document (11 interfaces) |
| [State Machine](docs/StateMachine.md) | State definitions, events, and transitions |
| [Hardware](docs/Hardware.md) | Hardware specification, GPIO pinout, BOM |
| [Implementation](docs/Implementation.md) | Code structure, config schema, templates |
| [Pi Setup](docs/PiSetup.md) | Raspberry Pi setup and deployment guide |
| [Test Plan](docs/TestPlan.md) | 53 test cases with pass/fail criteria |
| [RTM](docs/RTM.md) | Requirements Traceability Matrix (97% coverage) |

## Requirements

- **Phone:** Android 11+ with Wireless Android Auto
- **Pi:** Raspberry Pi 4 Model B (4 GB)
- **OS:** Raspberry Pi OS Lite Trixie 64-bit
- **Python:** 3.11+
- **OpenAuto/aasdk:** Built from source via `scripts/build-openauto.sh` (submodules included)
- **Bluetooth speaker:** Any A2DP-capable audio receiver

## Project Status

This is a prototype/hobbyist project. The full specification suite is complete and the system is operational end-to-end.

**Working:**
- Full wireless Android Auto projection (BLE discovery → WiFi → TCP/TLS → video + audio)
- Auto-reconnect — HFP Hands-Free profile triggers Android's car mode; AA launches in <2 s on boot, retries every 30 s until phone is in range; no user interaction required
- Fast disconnect detection — returns to splash screen within ~3 s of phone ending AA session; four independent signals cover all disconnect paths (in-app disconnect, BT settings, phone reboot, range loss)
- Bluetooth speaker discovery and pairing (BR/EDR via dbus-next, touchscreen UI)
- 4 concurrent AA audio streams (media, guidance, system, telephony) via PipeWire → BT A2DP
- AVRCP volume sync (phone volume rocker → PipeWire → BT speaker) using `dbus_next` async API
- WiFi AP+STA on single radio (concurrent AP for phone and infrastructure WiFi for SSH), managed by NetworkManager
- Splash screen with status display and BT Setup UI
- State machine orchestration with full error recovery and retry logic
- GSTVideoOutput — plain GStreamer C API (no qt-gstreamer); leaky post-decoder queue eliminates touch-to-screen latency; 30 FPS QTimer paint loop
- Audio stutter fix — RtAudio static mutex eliminates concurrent buffer access race
- Touch double-tap fix — libinput only; synthetic mouse events suppressed for touchscreen contacts
- Hardened error routing — unhandled exceptions in TCP_CONNECT/PROJECTION_ACTIVE enter ERROR_RECOVERY (retry) instead of SHUTDOWN
- Config files written to `/run/piauto/` (tmpfs); WiFi password has no hardcoded default
- Configurable logging (journald on Pi, stderr on dev) via `PIAUTO_LOG_LEVEL`

**Setup note:** Full power-loss resilience requires the BlueZ bind mount to be configured (`/data/bluetooth/ → /var/lib/bluetooth/`). See [Pi Setup Guide §6](docs/PiSetup.md) for the exact commands. Without this, an unexpected power cut will force re-pairing of phone and speaker on next boot.

See [Known Issues](docs/TestPlan.md#27-known-issues) for full details.

## License

This project is provided as-is for educational and hobbyist use.
