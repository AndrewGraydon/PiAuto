# Implementation Guide: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-IG-001                |
| Version        | 1.0                          |
| Date           | 2026-03-27                   |
| Status         | Draft                        |

## 1. Introduction

This document provides the implementation details needed to build PiAuto from the specification documents. It bridges the gap between "what the system shall do" (SRS, ICD) and "how to write the code."

### 1.1 References

| ID              | Document                         |
| :-------------- | :------------------------------- |
| PiAuto-SRS-001  | Software Requirements Specification |
| PiAuto-ARCH-001 | Architecture Document            |
| PiAuto-ICD-001  | Interface Control Document       |
| PiAuto-SM-001   | State Machine Specification      |
| PiAuto-HW-001   | Hardware & Power Specification   |

---

## 2. OpenAuto / aasdk — Wireless AA Feasibility

### 2.1 Critical Note

The upstream OpenAuto (`bluewave-studio/openauto`) was originally designed for **wired USB** Android Auto via AOAP. Wireless Android Auto support (BLE discovery + WiFi AP + TCP listener) requires modifications to aasdk's transport layer.

### 2.2 Recommended Approach

| Option | Description | Effort |
| :----- | :---------- | :----- |
| **A. Fork aasdk with TCP transport** | Modify `aasdk/transport` to add a `TCPTransport` alongside the existing `USBTransport`. The BLE handshake and WiFi AP are managed by the Python orchestrator; aasdk only needs to accept an incoming TCP connection on port 5288 instead of opening a USB endpoint. | Medium |
| **B. Use Crankshaft as reference** | Crankshaft achieved wireless AA on earlier versions. Study their patches to aasdk/OpenAuto for TCP transport support. | Medium |
| **C. Build a TCP-to-USB bridge** | Run aasdk in USB mode but use a virtual USB gadget (via Linux configfs) to pipe TCP data into a fake USB endpoint. This is effectively what WirelessAndroidAutoDongle does. | High complexity, fragile |

**Recommendation:** Option A. The aasdk library has a clean transport abstraction (`ITransport` interface). Adding a TCP transport that wraps a connected socket is straightforward. The Python orchestrator handles everything before the TCP connection (BLE, WiFi, DHCP) and then hands off to OpenAuto.

### 2.3 Pinned Versions

| Component | Repository | Commit/Tag |
| :-------- | :--------- | :--------- |
| aasdk     | `opencardev/aasdk` | Pin to latest stable tag at build time. Document the exact commit in the build script. |
| OpenAuto  | `opencardev/openauto` or custom fork | Same — pin at build time. |

**Build script must record:** `git rev-parse HEAD` for both repos, stored in `/data/build-info.txt`.

---

## 3. Python Package Structure

```
piauto/
├── __init__.py
├── __main__.py              # Entry point: python -m piauto
├── statemachine.py          # State machine (SM-001 implementation)
├── ble.py                   # BLE WAA advertising and handshake (BlueZ D-Bus)
├── bt_pair.py               # BR/EDR discovery and pairing via dbus-next
├── wifi.py                  # hostapd + dnsmasq management
├── gpio.py                  # Ignition sense (GPIO 17) + fan PWM (GPIO 4)
├── thermal.py               # Temperature monitoring + fan profile
├── config.py                # YAML config loader + validator
├── openauto.py              # OpenAuto process launcher + monitor
├── splash.py                # Splash screen app + BT speaker setup UI
├── clock.py                 # System time initialization (FR-042)
└── logging.py               # journald logging setup
```

Entry point:

```python
# __main__.py
from piauto.statemachine import StateMachine

def main():
    sm = StateMachine()
    sm.run()  # blocking event loop

if __name__ == "__main__":
    main()
```

---

## 4. YAML Configuration Schema

File: `/data/piauto.yaml`

```yaml
# PiAuto Configuration — see PiAuto-SRS-001 NR-006

wifi:
  ssid: "PiAuto"              # AP network name (string, 1-32 chars)
  password: "changeme1"        # WPA2-PSK passphrase (string, min 8 chars)
  channel: 149                 # 5 GHz channel (149 or 165)

bluetooth:
  device_name: "PiAuto"       # BLE advertising name
  max_paired: 8               # Max stored phone pairing records (1-8)
  speaker_mac: ""             # Preferred A2DP speaker MAC (empty = auto)

display:
  resolution: "800x480"       # Display resolution (informational — matches config.txt)
  fps: 30                     # Target frame rate

audio:
  output: "bluetooth"          # "bluetooth" or "alsa" or "usb"

thermal:
  fan_low_temp: 50            # °C — fan starts at 50% PWM
  fan_high_temp: 65           # °C — fan goes to 100% PWM
  hysteresis: 3               # °C — prevents rapid fan cycling
  poll_interval: 5            # seconds

power:
  ignition_debounce_ms: 500   # GPIO 17 debounce time
  shutdown_timeout_s: 10      # Max seconds for clean shutdown

openauto:
  binary: "/usr/local/bin/openauto"  # Path to OpenAuto binary
  extra_args: []              # Additional command-line arguments
```

### 4.1 Validation Rules

| Field | Type | Constraint |
| :---- | :--- | :--------- |
| `wifi.ssid` | string | 1–32 characters |
| `wifi.password` | string | Minimum 8 characters |
| `wifi.channel` | int | Must be 149 or 165 |
| `bluetooth.max_paired` | int | 1–8 |
| `bluetooth.speaker_mac` | string | Empty or valid MAC format (XX:XX:XX:XX:XX:XX) |
| `thermal.fan_low_temp` | int | 30–75 |
| `thermal.fan_high_temp` | int | fan_low_temp+5 to 80 |

### 4.2 Missing/Corrupt Config Handling

If `/data/piauto.yaml` is missing or fails validation, the system shall:

1. Log a warning to journald.
2. Use built-in defaults (as shown above).
3. Continue booting normally.

---

## 5. systemd Service File

File: `/etc/systemd/system/piauto.service`

```ini
[Unit]
Description=PiAuto - Wireless Android Auto Head Unit
After=bluetooth.target pipewire.service wireplumber.service
Wants=bluetooth.target pipewire.service wireplumber.service

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 -m piauto
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Note:** Runs as root because it needs GPIO access (libgpiod) and must manage hostapd/dnsmasq (which require root). OpenAuto is launched as the `piauto` user via `subprocess` with `User=piauto`.

---

## 6. hostapd Configuration Template

File generated dynamically at `/tmp/hostapd.conf` by `piauto.wifi` module:

> **Note:** In AP+STA mode, the AP runs on `uap0` (virtual interface) while `wlan0` remains connected to infrastructure WiFi. In standalone AP mode (production), `wlan0` is used directly.

```ini
interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=a
channel={channel}
ieee80211n=1
ieee80211ac=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
country_code={country}
max_num_sta=1
```

Placeholders `{ssid}`, `{channel}`, `{password}`, `{country}` are filled from the YAML config.

---

## 7. dnsmasq Configuration Template

File generated at `/tmp/dnsmasq.conf`:

```ini
interface=wlan0
dhcp-range=192.168.1.100,192.168.1.199,255.255.255.0,1h
bind-interfaces
no-resolv
no-daemon
```

---

## 8. TLS Certificate Generation

At first boot (if `/data/tls/cert.pem` does not exist), the `piauto-main` service generates a self-signed certificate:

```bash
mkdir -p /data/tls
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -keyout /data/tls/key.pem -out /data/tls/cert.pem \
  -days 36500 -nodes \
  -subj "/CN=PiAuto"
chmod 600 /data/tls/key.pem
```

| Parameter     | Value                     |
| :------------ | :------------------------ |
| Algorithm     | ECDSA (P-256)             |
| Validity      | 100 years (no expiry concerns without RTC) |
| Key file      | `/data/tls/key.pem` (mode 0600) |
| Cert file     | `/data/tls/cert.pem`     |
| Subject       | CN=PiAuto                 |

---

## 9. Splash Screen Application

### 9.1 Technology

A minimal Python script using **PyQt5** with `QT_QPA_PLATFORM=eglfs`. It displays a full-screen window with status text, a "Setup" button, and supports a dedicated Bluetooth speaker pairing UI mode.

### 9.2 IPC with State Machine

The splash app communicates with the state machine via **stdout lines**. The state machine reads these with `SplashManager.read_stdout_line()`:

| Signal | Meaning | Triggered By |
| :----- | :------ | :----------- |
| `SETUP` | User tapped the Setup button | Idle splash screen |
| `BACK` | User tapped Back in BT setup | BT setup UI |
| `PAIRED:mac:name` | Successfully paired a BT speaker | BT setup UI |

**Modes:**

```bash
# Status display (IDLE, BT_PAIRING, WIFI_WAIT, etc.):
QT_QPA_PLATFORM=eglfs python3 -m piauto.splash "Waiting for phone..."

# Bluetooth speaker setup UI:
QT_QPA_PLATFORM=eglfs python3 -m piauto.splash --bt-setup
```

### 9.3 Bluetooth Speaker Setup UI

The `--bt-setup` mode provides a touchscreen-driven UI for BR/EDR speaker discovery and pairing:

1. User taps "Scan" — launches `piauto.bt_pair scan` via QProcess
2. Discovered devices appear as tappable buttons
3. User taps a device — launches `piauto.bt_pair pair MAC` via QProcess
4. On success (`PAIR_OK`), writes `PAIRED:mac:name` to stdout for the state machine

> **Note:** The bt_pair subprocess is launched as the `pi` user via `sudo -u pi` because BR/EDR discovery as root misses some devices due to BlueZ D-Bus policy differences.

### 9.3 DRM Master Handoff

1. `piauto-main` sends SIGTERM to the splash process.
2. Splash catches SIGTERM, releases DRM master, exits.
3. `piauto-main` waits for splash to exit (confirmed via `process.wait()`).
4. `piauto-main` launches OpenAuto, which acquires DRM master.

A 500 ms sleep between splash exit and OpenAuto launch provides a safety margin for DRM release. If OpenAuto fails to acquire DRM, it logs an error and exits (handled by ERROR_RECOVERY).

---

## 10. OpenAuto Process Management

### 10.1 Launching

```python
import subprocess

proc = subprocess.Popen(
    [config.openauto.binary] + config.openauto.extra_args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env={
        **os.environ,
        "QT_QPA_PLATFORM": "eglfs",
        "QT_QPA_EGLFS_KMS_CONFIG": "/data/eglfs.json",
    },
    user="piauto",
)
```

### 10.2 Monitoring

The state machine polls `proc.poll()` in its event loop (every 100 ms). When OpenAuto exits:

| Exit Code | Interpretation | Action |
| :-------- | :------------- | :----- |
| 0 | Clean disconnect | → IDLE |
| Non-zero | Error / connection lost | → ERROR_RECOVERY |
| None (SIGTERM) | Killed by state machine | Expected (shutdown) |

### 10.3 Detecting "Projection Active"

The state machine reads OpenAuto's **stderr** line-by-line in a background thread. It searches for a known log pattern indicating projection has started (e.g., `"Projection started"` or `"Service: MediaSink started"`). The exact pattern must be determined from the OpenAuto source and documented in the build notes.

If no "projection active" log is detected within the TCP_CONNECT timeout (30 s), the state machine treats it as `OpenAutoFailed`.

---

## 11. System Time (No RTC)

The Pi 4 has no battery-backed RTC. Without NTP (the Pi is an AP, not a client), the clock resets to the epoch on every boot.

### 11.1 Fake-hwclock Approach

Install `fake-hwclock` package, which saves the current time to `/data/clock` on shutdown and restores it on boot. This ensures:

- Log timestamps are monotonically increasing across boots.
- TLS cert validation does not fail due to clock skew (cert is self-signed with 100-year validity, so any date after 2026 is valid).

### 11.2 Implementation

```bash
# On shutdown (in piauto-main SHUTDOWN handler):
date +%s > /data/clock

# On boot (in piauto-main BOOTING handler):
if [ -f /data/clock ]; then
    date -s @$(cat /data/clock)
fi
```

---

## 12. Country Code / Regulatory Domain

The 5 GHz WiFi channel selection depends on the regulatory domain. The system must set the country code for hostapd.

Add to `/data/piauto.yaml`:

```yaml
wifi:
  country: "AU"   # ISO 3166-1 alpha-2 country code
```

This is passed to hostapd as `country_code=AU` and to the kernel via `iw reg set AU`.

**Note for the developer:** Channel 149 and 165 are available in most regions (US, AU, EU, JP) but not all. If the chosen channel is unavailable, hostapd will fail to start — the state machine should catch this and log a clear error.
