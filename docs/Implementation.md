# Implementation Guide: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-IG-001                |
| Version        | 1.1                          |
| Date           | 2026-03-29                   |
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
| **A. Fork aasdk with TCP transport** | Modify `aasdk/transport` to add a `TCPTransport` alongside the existing `USBTransport`. The BLE/RFCOMM credential exchange and WiFi AP are managed by the Python orchestrator; aasdk only needs to accept an incoming TCP connection on port 5000 instead of opening a USB endpoint. | Medium |
| **B. Use Crankshaft as reference** | Crankshaft achieved wireless AA on earlier versions. Study their patches to aasdk/OpenAuto for TCP transport support. | Medium |
| **C. Build a TCP-to-USB bridge** | Run aasdk in USB mode but use a virtual USB gadget (via Linux configfs) to pipe TCP data into a fake USB endpoint. This is effectively what WirelessAndroidAutoDongle does. | High complexity, fragile |

**Recommendation:** Option A. The aasdk library has a clean transport abstraction (`ITransport` interface). Adding a TCP transport that wraps a connected socket is straightforward. The Python orchestrator handles everything before the TCP connection (BLE, WiFi, DHCP) and then hands off to OpenAuto.

### 2.3 Pinned Versions

| Component | Repository | Branch | Notes |
| :-------- | :--------- | :----- | :---- |
| aasdk     | `AndrewGraydon/aasdk` | `piauto-debian13` | Fork of OpenDsh/aasdk with OpenSSL 3.x compatibility patch. Commit `7f84303`. |
| OpenAuto  | `AndrewGraydon/openauto` | `piauto-debian13` | Fork of OpenDsh/openauto with GSTVideoOutput rewrite, RtAudio 6.x fix, and QGst removal. Commits `13bae52`, `ee75ebc`. |

**Build script must record:** `git rev-parse HEAD` for both repos, stored in `/data/build-info.txt`.

### 2.4 OpenDsh Fork Evaluation (2026-03-28)

The [OpenDsh](https://github.com/openDsh) project maintains actively-developed forks of both aasdk and openauto. Key differences from `opencardev`:

| Feature | opencardev | OpenDsh |
| :------ | :--------- | :------ |
| Last commit | 2023 (limbo) | 2024 (active) |
| RtAudio thread safety | No mutex — concurrent buffer access causes stutter | PR #32 adds static mutex serializing RtAudio calls |
| OpenSSL 3.x support | No (uses deprecated OpenSSL 1.x APIs) | No (same issue) |
| RtAudio 6.x support | No (`RtAudioError` class removed in v6) | No (same issue) |
| Qt5GStreamer dependency | Uses QGlib for GStreamer video output | Same — requires qt-gstreamer | **Resolved** — rewritten to plain GStreamer C API (see patches below) |

**Build patches applied (all committed to `AndrewGraydon/aasdk` and `AndrewGraydon/openauto` branch `piauto-debian13`):**

1. **aasdk SSLWrapper.cpp** — Wrapped deprecated OpenSSL 1.x calls (`FIPS_mode_set`, `ENGINE_cleanup`, `ERR_load_BIO_strings`, etc.) in `#if (OPENSSL_VERSION_NUMBER < 0x30000000L)` guards. Commit `7f84303` on `AndrewGraydon/aasdk`.
2. **openauto RtAudioOutput.cpp** — Changed `catch(const RtAudioError& e)` to `catch(const std::exception& e)` for RtAudio 6.x compatibility. Also incorporates OpenDsh PR #32 static mutex (`std::mutex RtAudioOutput::mutex_`) for audio stutter fix.
3. **h264bitstream** — Built from source (`github.com/aizvorski/h264bitstream`) as it is not packaged in Debian 13.
4. **openauto GSTVideoOutput.cpp / GSTVideoOutput.hpp** — Complete rewrite. Removed all QGlib/QGst dependencies. New implementation uses the plain GStreamer C API (`gst-1.0`). Full pipeline string:
   ```
   appsrc ! queue(max-size-buffers=2) ! h264parse ! capssetter(colorimetry=bt709) !
   <v4l2h264dec or avdec_h264> ! queue(max-size-buffers=1, leaky=downstream) !
   videocrop ! videoconvert ! video/x-raw,format=RGB ! appsink(sync=false, drop=true)
   ```
   A new `VideoWidget` (QPainter-based `QWidget`) renders RGB frames delivered by `GstAppSink` via `onNewSample` callback. A 30 FPS `QTimer` on the Qt main thread drives `VideoWidget` repaints — this decouples the GStreamer decode callback thread from the Qt event queue, preventing event loop stalls. Qt retains DRM master throughout; GStreamer only decodes. The `newFrame` signal is connected via `Qt::QueuedConnection` for thread safety between the GStreamer thread and Qt main thread. Decoder selection is automatic: system attempts `v4l2h264dec` (Pi hardware), then `avdec_h264` (software fallback).

   **Queue latency fix:** The default GStreamer queue holds 200 buffers, causing 3–8 second touch-to-screen latency (the pipeline drains the backlog before rendering the current frame). The post-decoder queue uses `max-size-buffers=1, leaky=downstream` to drop stale frames and always surface the latest. The pre-decoder queue is capped at `max-size-buffers=2` to limit decoder input buffering.

   **EGLFS window stacking fix:** `onStartPlayback()` hides `MainWindow` before showing `VideoWidget` at full-screen geometry. On Qt EGLFS, the first top-level window shown becomes "primary" and a second window with `WindowStaysOnTopHint` cannot be raised above it via `raise()`. Hiding `MainWindow` first avoids this constraint.
5. **openauto CMakeLists.txt** — Removed `find_package(Qt5GStreamer)`, removed `Qt5::Quick`, `Qt5::Qml`, `Qt5::QuickWidgets`, and `${QTGSTREAMER_*}` link targets. Added `pkg_check_modules(GST REQUIRED gstreamer-1.0 gstreamer-sdp-1.0 gstreamer-video-1.0 gstreamer-app-1.0)`. Removed `QGst::init()` call from ServiceFactory.cpp (replaced with `gst_init(nullptr, nullptr)`). The `-DGST_BUILD=ON` cmake flag (note: `ON` not `TRUE`) enables this path.

### 2.5 Build and Install Paths

After building, the artifacts are:

| Artifact | Path |
| :------- | :--- |
| OpenAuto source | `/opt/openauto/` |
| Build directory | `/opt/openauto/build/` |
| Binary (post-build) | `/opt/openauto/bin/autoapp` |
| Shared library | `/opt/openauto/lib/libopenauto.so.2` |
| Installed binary (production) | `/usr/local/bin/autoapp` |

The RPATH embedded in `autoapp` points to `/opt/openauto/lib/`, so `libopenauto.so.2` is found there at runtime. After each build, copy the binary to the production path and restart the service:

```bash
cmake -S /opt/openauto -B /opt/openauto/build \
    -DCMAKE_BUILD_TYPE=Release -DGST_BUILD=ON \
    && cmake --build /opt/openauto/build -j2
cp /opt/openauto/bin/autoapp /usr/local/bin/autoapp
systemctl restart piauto
```

---

## 3. Python Package Structure

```
piauto/
├── __init__.py
├── __main__.py              # Entry point: python -m piauto
├── statemachine.py          # State machine (SM-001 implementation)
├── ble.py                   # BLE WAA advertising + RFCOMM credential exchange (BlueZ D-Bus)
├── bt_pair.py               # BR/EDR discovery and pairing via dbus-next
├── wifi.py                  # hostapd + dnsmasq management (auto-detects AP+STA vs standalone)
├── gpio.py                  # Ignition sense (GPIO 17) + fan PWM (GPIO 4)
├── thermal.py               # Temperature monitoring + fan profile
├── config.py                # YAML config loader + validator
├── openauto.py              # OpenAuto process launcher + monitor
├── splash.py                # Splash screen app + BT speaker setup UI
├── volume.py                # AVRCP→PipeWire volume sync
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
  binary: "/usr/local/bin/autoapp"   # Path to OpenAuto binary
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

> **Note:** The `{interface}` placeholder is auto-detected at runtime. In AP+STA mode (`uap0` exists), the AP runs on `uap0` while `wlan0` remains connected to infrastructure WiFi. In standalone AP mode, `wlan0` is used directly.

```ini
interface={interface}
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

| Placeholder   | Source                                                                   |
| :------------ | :----------------------------------------------------------------------- |
| `{interface}` | Auto-detected: `uap0` (AP+STA) or `wlan0` (standalone)                  |
| `{ssid}`      | `wifi.ssid` from YAML config                                            |
| `{channel}`   | `wifi.channel` from YAML config                                         |
| `{password}`  | `wifi.password` from YAML config                                        |
| `{country}`   | `wifi.country` from YAML config                                         |

---

## 7. dnsmasq Configuration Template

File generated at `/tmp/dnsmasq.conf`:

```ini
interface={interface}
dhcp-range={dhcp_start},{dhcp_end},255.255.255.0,1h
bind-interfaces
no-resolv
no-daemon
log-dhcp
```

| Mode       | Interface | AP IP          | DHCP Range                          |
| :--------- | :-------- | :------------- | :---------------------------------- |
| AP+STA     | `uap0`    | 192.168.50.1   | 192.168.50.100 – 192.168.50.199    |
| Standalone | `wlan0`   | 192.168.1.1    | 192.168.1.100 – 192.168.1.199      |

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

A single long-lived Python script using **PyQt5** with `QT_QPA_PLATFORM=eglfs`. It uses a `QStackedWidget` to switch between views (idle splash, BT setup) without releasing DRM master, preventing console text from flashing between transitions.

### 9.2 Single-Process Architecture

The splash runs as **one process for its entire lifetime**. The state machine sends commands via **stdin** to switch views, and the splash sends signals back via **stdout**:

**Commands (state machine → splash, via stdin):**

| Command | Effect |
| :------ | :----- |
| `STATUS\|text` | Switch to idle view, display `text` |
| `BT_SETUP` | Switch to Bluetooth speaker setup view |

**Signals (splash → state machine, via stdout):**

| Signal | Meaning | Triggered By |
| :----- | :------ | :----------- |
| `SETUP` | User tapped the Setup button | Idle splash screen |
| `BACK` | User tapped Back in BT setup | BT setup UI |
| `PAIRED\|mac\|name` | Successfully paired a BT speaker | BT setup UI |

> **Why a single process?** Qt EGLFS acquires exclusive DRM master. If you kill one Qt process and launch another, the Linux VT console is briefly visible between DRM master release and reacquisition. A `QStackedWidget` switches views instantly within the same process, keeping the display seamless.

**Launch:**

```bash
QT_QPA_PLATFORM=eglfs python3 -m piauto.splash
# Optionally pass initial status text as arg:
QT_QPA_PLATFORM=eglfs python3 -m piauto.splash "Starting..."
```

### 9.3 Bluetooth Speaker Setup UI

The BT setup view (activated by `BT_SETUP` stdin command) provides a touchscreen-driven UI for BR/EDR speaker discovery and pairing:

1. User taps "Scan" — launches `piauto.bt_pair scan` via QProcess
2. Discovered devices appear as tappable buttons
3. User taps a device — launches `piauto.bt_pair pair MAC` via QProcess
4. On success (`PAIR_OK`), writes `PAIRED|mac|name` to stdout for the state machine
5. User taps "Back" — writes `BACK` to stdout, splash switches back to idle view

> **Note:** The bt_pair subprocess is launched as the `pi` user via `sudo -u pi` because BR/EDR discovery as root misses some devices due to BlueZ D-Bus policy differences.

### 9.4 SIGTERM Handling

Qt's event loop blocks in `app.exec_()`, so SIGTERM cannot call `app.quit()` directly from a signal handler. The splash uses a `socket.socketpair()` with a `QSocketNotifier` to relay SIGTERM into Qt's event loop safely.

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
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
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

---

## 13. Volume Sync Module

Module: `piauto/volume.py`

The volume sync module bridges AVRCP volume reports from the phone to PipeWire on the Pi.

| Parameter | Value |
| :-------- | :---- |
| BlueZ property | `org.bluez.MediaTransport1.Volume` |
| Poll interval | 0.3 s |
| Input range | 0–127 (AVRCP) |
| Output range | 0.0–1.0 (PipeWire linear) |
| Sink command | `wpctl set-volume @DEFAULT_AUDIO_SINK@ <value>` |

The state machine starts the volume polling task on entry to `PROJECTION_ACTIVE` and cancels it on exit. The module runs as an `asyncio.Task` that reads the D-Bus property, maps the value linearly (`volume / 127`), and calls `wpctl` only when the value changes.

---

## 14. BT_PAIRING Timing and Port Readiness

The BT_PAIRING → PROJECTION_ACTIVE transition has a strict ordering requirement. The phone attempts a TCP connection to port 5000 **immediately** after receiving RFCOMM credentials, so the port must already be listening.

**Sequence:**

1. State machine starts the WiFi AP (hostapd + dnsmasq).
2. State machine launches OpenAuto (`autoapp`).
3. State machine polls `ss -tlnH sport = :5000` until the port is listening. OpenAuto typically takes ~6 s to bind port 5000.
4. **Only after port 5000 is confirmed listening**, the state machine sends WiFi credentials over the RFCOMM channel.
5. Phone connects to the AP, then opens a TCP connection to port 5000.

If port 5000 does not become ready within the TCP_CONNECT timeout (30 s), the state machine fires `OpenAutoFailed` and transitions to ERROR_RECOVERY.

---

## 15. PipeWire Environment for OpenAuto

OpenAuto (`autoapp`) runs as root but must use the PipeWire/PulseAudio daemon running as user `pi` (uid 1000). The following environment variables must be set in the OpenAuto subprocess:

```python
env={
    **os.environ,
    "QT_QPA_PLATFORM": "eglfs",
    "QT_QPA_EGLFS_KMS_CONFIG": "/data/eglfs.json",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
}
```

| Variable | Purpose |
| :------- | :------ |
| `XDG_RUNTIME_DIR` | Points to user `pi`'s runtime dir so PipeWire clients find the correct socket |
| `PULSE_SERVER` | Directs PulseAudio-compatible clients (Qt multimedia) to the PipeWire-Pulse socket owned by uid 1000 |

**Prerequisite:** `loginctl enable-linger pi` must be enabled so that PipeWire and WirePlumber start at boot for user `pi` regardless of login state.

---

## 16. GStreamer Packages

OpenAuto uses Qt Multimedia, which delegates to GStreamer for media decoding. The following packages are required on Debian Trixie:

| Package | Purpose |
| :------ | :------ |
| `libqt5multimedia5-plugins` | Qt Multimedia → GStreamer bridge (provides the GStreamer QMediaService plugin) |
| `gstreamer1.0-libav` | H.264 video decoder (libav/ffmpeg-based) used for the AA video stream |
| `gstreamer1.0-plugins-bad` | Additional decoders and parsers (e.g., `h264parse`) |
| `gstreamer1.0-plugins-ugly` | Patent-encumbered codecs that may be needed for certain audio streams |

Install:

```bash
apt install libqt5multimedia5-plugins gstreamer1.0-libav \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

---

## 17. Qt Touch Input Configuration (EGLFS)

### 17.1 Problem: libinput Double-Tap

Qt EGLFS auto-loads the `libinput` plugin by default, which reads the USB touchscreen (wch.cn USB2IIC_CTP_CONTROL) and registers it as **both a pointer device and a touch device**. This causes every physical tap to generate two events — one from each device registration — resulting in double-tap behavior in OpenAuto.

### 17.2 Fix: Disable libinput, Use evdevtouch with Exclusive Grab

Set `QT_QPA_EGLFS_NO_LIBINPUT=1` in the subprocess environment to prevent Qt from loading libinput. Then specify the `evdevtouch` plugin explicitly with the `:grab` parameter to claim exclusive access to the device node, preventing any other process from also reading it.

This applies to **both** the splash screen (`splash.py`) and OpenAuto (`openauto.py`) subprocess environments:

```python
env={
    **os.environ,
    "QT_QPA_PLATFORM": "eglfs",
    "QT_QPA_EGLFS_KMS_CONFIG": "/data/eglfs.json",
    "QT_QPA_EGLFS_NO_LIBINPUT": "1",
    "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS": "/dev/input/by-id/usb-wch.cn_USB2IIC_CTP_CONTROL-event-if00:rotate=0:grab",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
}
```

| Variable | Purpose |
| :------- | :------ |
| `QT_QPA_EGLFS_NO_LIBINPUT` | Disables libinput plugin; Qt falls back to evdev input plugins |
| `QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS` | Specifies the touch device path and activates `:grab` for exclusive access |

**Note:** The `:grab` parameter uses the kernel `EVIOCGRAB` ioctl to ensure the device node is owned exclusively by the Qt process. The device path (`/dev/input/by-id/...`) should use a stable symlink rather than a volatile `eventN` index.

---

## 18. Phone Disconnect — Return to Splash

### 18.1 Problem: autoapp Does Not Exit on Phone Disconnect

When the phone disconnects from an active Android Auto session, `autoapp` (OpenAuto) does **not** exit. It remains running and displays its own internal waiting screen. This means the state machine's existing mechanism of watching for process exit (exit code 0 → `PhoneDisconnected`) does not fire — the system stays in PROJECTION_ACTIVE indefinitely.

### 18.2 Fix: Watch for Projection-Stopped Log Patterns

`OpenAutoManager` exposes a `wait_for_projection_stopped()` async method that monitors `autoapp`'s stderr in a background task. It searches for known log patterns that indicate the AA session has ended:

| Log Pattern | Source |
| :---------- | :----- |
| `onAndroidAutoQuit` | OpenAuto AA session lifecycle callback |
| `[WifiProjectionService] stop()` | aasdk WifiProjectionService teardown |

When either pattern is detected, the state machine:

1. Sends SIGTERM to `autoapp` (killing the lingering process).
2. Re-launches the splash screen (which reacquires DRM master).
3. Transitions to IDLE, resetting for the next connection.

### 18.3 Implementation Notes

- `wait_for_projection_stopped()` runs as an `asyncio.Task` started on entry to PROJECTION_ACTIVE and cancelled on exit.
- The method reads `autoapp`'s stderr line-by-line asynchronously; it does not poll — it streams.
- The exact log patterns may vary across OpenAuto builds. The pattern list should be updated in the build notes after verifying against the pinned OpenAuto commit (see §2.3).
- If `autoapp` exits on its own (exit code 0 or 1) before `wait_for_projection_stopped()` fires, the existing exit-code logic takes precedence and the task is cancelled.

---

## 19. D-Bus Policy for WirePlumber Bluetooth

WirePlumber (running as user `pi`, uid 1000) must communicate with `bluetoothd` (running as root) over the system D-Bus to manage A2DP sink/source endpoints. Without an explicit policy file, D-Bus denies these calls.

File: `/etc/dbus-1/system.d/wireplumber-bluetooth.conf`

```xml
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
  "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="pi">
    <allow send_destination="org.bluez"/>
  </policy>
</busconfig>
```

This grants user `pi` permission to send method calls and property reads to the BlueZ D-Bus service. After installing this file, reload the D-Bus daemon:

```bash
systemctl reload dbus
```
