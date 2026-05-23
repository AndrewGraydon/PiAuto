# Implementation Guide: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-IG-001                |
| Version        | 1.6                          |
| Date           | 2026-05-23                   |
| Status         | Active                       |

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
  password: "yourpassword"     # WPA2-PSK passphrase (string, min 8 chars) — required, no default
  channel: 149                 # 5 GHz channel (149 or 165)

bluetooth:
  device_name: "PiAuto"       # BLE advertising name
  max_paired: 8               # Max stored phone pairing records (1-8)
  speaker_mac: ""             # Preferred A2DP speaker MAC (empty = auto)

display:
  resolution: "1024x600"      # Physical display native resolution (informational). AA stream is 800×480; VideoWidget scales to fill display.
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

File generated dynamically at `/run/piauto/hostapd.conf` by `piauto.wifi` module. The directory `/run/piauto/` is created on demand and lives on tmpfs — it is not world-readable like `/tmp`, and is cleaned up automatically on reboot:

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

File generated at `/run/piauto/dnsmasq.conf`:

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

### 9.4 bt_pair: Why Connect() Is Not Called

`bt_pair pair` intentionally does **not** call `Device1.Connect()` after pairing and trusting the device. Instead it waits for WirePlumber to auto-connect and reports `PAIR_OK` regardless.

**Background:** An earlier version called `Device1.Connect()` after `Device1.Pair()`. This worked on simple A2DP-only speakers but caused **bluetoothd 5.82 to SEGV** on devices that advertise multiple profiles simultaneously (e.g. Logi Dock, which advertises both HFP and A2DP). The crash occurred inside BlueZ's AVDTP codec negotiation — `bt_pair` was calling `Connect()` at the same moment WirePlumber was registering its A2DP endpoints, creating a race in BlueZ's internal profile state machine.

**Current approach:**
1. `Device1.Pair()` — exchange link keys
2. `Trusted = True` — required for WirePlumber `auto-connect` and for BlueZ `AutoConnect` to fire reliably
3. Wait up to 12 s for `Device1.Connected` to go `True` (WirePlumber auto-connects via `bluez5.auto-connect = [ a2dp_sink ]`)
4. Report `PAIR_OK` whether or not WirePlumber connected within the window — a paired and trusted device will connect automatically on next boot or range entry

This avoids the SEGV, removes the race with WirePlumber, and correctly separates concerns: bt_pair owns pairing; WirePlumber owns audio device connections.

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

## 11.3 Periodic Clock Save During Projection

**Problem:** `save_time()` was only called on clean shutdown. A mid-session power cut left `/data/clock` with the timestamp from the previous clean shutdown — potentially hours behind the actual time. On next boot, `restore_time()` would set the system clock to that stale value, which could cause TLS handshake failures if the clock predates any peer certificate's `notBefore` date.

**Solution:** A `_periodic_clock_save()` coroutine in `statemachine.py` loops: sleep `CLOCK_SAVE_INTERVAL_S` seconds, call `save_time()`, log debug. It runs as a fourth `asyncio.Task` alongside `exit_task`, `stopped_task`, and `ignition_task` during PROJECTION_ACTIVE, grouped in a single `asyncio.wait(return_when=FIRST_COMPLETED)` call. When any other task completes (disconnect, ignition off), the `clock_task` is cancelled as part of the standard pending-task cleanup.

| Constant | Value | Location |
| :------- | :---- | :------- |
| `CLOCK_SAVE_INTERVAL_S` | 300 | `statemachine.py` line ~80 |

After this change, the clock file is at most 5 minutes stale after a power cut (down from hours).

**PROJECTION_ACTIVE asyncio.wait pattern (9 tasks — see §18.2 and §22 for the full set):**

```python
exit_task          = asyncio.create_task(self._openauto.wait_for_exit())
stopped_task       = asyncio.create_task(self._openauto.wait_for_projection_stopped())
ignition_task      = asyncio.create_task(self._ignition_off.wait())
clock_task         = asyncio.create_task(self._periodic_clock_save())
rfcomm_task        = asyncio.create_task(self._ble.wait_for_rfcomm_reconnect_attempt())
bt_disconnect_task = asyncio.create_task(self._ble.wait_for_phone_disconnect(mac))
wifi_leave_task    = asyncio.create_task(self._wifi.wait_for_client_leave_ap())
tcp_end_task       = asyncio.create_task(self._openauto.wait_for_tcp_session_end())
bluez_task         = asyncio.create_task(self._ble.watch_bluez_restart())

done, pending = await asyncio.wait(
    [exit_task, stopped_task, ignition_task, clock_task,
     rfcomm_task, bt_disconnect_task, wifi_leave_task, tcp_end_task, bluez_task],
    return_when=asyncio.FIRST_COMPLETED,
)
for task in pending:
    task.cancel()
# Await cancellations so stray coroutines don't bleed into the next state
await asyncio.gather(*pending, return_exceptions=True)
```

`clock_task` never completes first in practice (it loops indefinitely). It is cancelled cleanly when one of the other tasks fires. All cancelled tasks are awaited before the state transition to prevent stray D-Bus or subprocess calls leaking into the next state.

---

## 11.4 BlueZ Pairing Persistence (Bind Mount)

**Problem:** Under overlayfs (read-only root), BlueZ writes its pairing database to `/var/lib/bluetooth/`. These writes go to the RAM overlay and are discarded on power cut. Every unexpected shutdown forces re-pairing of the phone and Bluetooth speaker.

**Solution:** Bind-mount `/data/bluetooth/` over `/var/lib/bluetooth/` so BlueZ's writes go directly to the persistent `/data` partition.

**One-time setup (after initial pairing, before enabling overlayfs):**

```bash
sudo mkdir -p /data/bluetooth
sudo cp -a /var/lib/bluetooth/. /data/bluetooth/
```

Add to `/etc/fstab`:

```
/data/bluetooth  /var/lib/bluetooth  none  bind  0  0
```

**Important:** Copy the existing BlueZ database before the bind mount takes effect, or all current pairings will be lost. This step must be completed before enabling overlayfs (see PiSetup §6.3). After a reboot, verify with:

```bash
mount | grep bluetooth
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
| D-Bus library | `dbus_next` (async) |

The state machine calls `await self._volume.start()` on entry to `PROJECTION_ACTIVE` and `await self._volume.stop()` on exit. The module runs as an `asyncio.Task` that:

1. Opens a single `dbus_next` system bus connection for its lifetime.
2. Calls `GetManagedObjects` on `org.bluez` asynchronously every 0.3 s — the event loop is **not blocked** during this call (contrast with the prior synchronous `python-dbus` approach which blocked the loop for every poll).
3. Maps each `MediaTransport1.Volume` value linearly (`volume / 127`).
4. Calls `wpctl` only when the value changes, with a guard (`_wpctl_proc`) that skips the call if a previous `wpctl` invocation is still running (prevents concurrent invocations when the poll interval is shorter than `wpctl` startup time).
5. Disconnects the bus cleanly on cancellation via a `finally` block.

**Dependency:** `dbus_next` (already a project dependency used by `ble.py` and `bt_pair.py`). The prior `python-dbus` (`python3-dbus`) dependency is **no longer required** for volume sync.

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

Qt EGLFS loads the `libinput` plugin, which reads the USB touchscreen (wch.cn USB2IIC_CTP_CONTROL) and delivers each physical touch as **both a `QTouchEvent` and a synthetic `QMouseEvent`**. The original `InputDevice::eventFilter` processed both, causing every tap to register twice in OpenAuto (double-tap behavior).

### 17.2 Fix: Skip Synthetic Mouse Events in InputDevice

libinput is retained for input handling (it provides correct multi-touch event delivery). The double-tap is fixed in `openauto/Projection/InputDevice.cpp` by skipping synthetic mouse events when a touchscreen is configured:

```cpp
else if (event->type() == QEvent::MouseButtonPress ||
         event->type() == QEvent::MouseButtonRelease ||
         event->type() == QEvent::MouseMove)
{
    // libinput synthesizes a QMouseEvent for each touch contact.
    // Skip these when a touchscreen is configured — only the
    // QTouchEvent path is used, preventing double-tap.
    if (configuration_->getTouchscreenEnabled())
        return false;
    return this->handleMouseEvent(event);
}
```

This fix is committed to `AndrewGraydon/openauto` branch `piauto-debian13`.

The OpenAuto subprocess environment uses:

```python
env={
    **os.environ,
    "QT_QPA_PLATFORM": "eglfs",
    "QT_QPA_EGLFS_KMS_CONFIG": "/data/eglfs.json",
    "QT_QPA_EGLFS_HIDECURSOR": "1",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
}
```

| Variable | Purpose |
| :------- | :------ |
| `QT_QPA_EGLFS_HIDECURSOR` | Hides the mouse cursor (irrelevant on a touchscreen) |

**Note:** `QT_QPA_EGLFS_NO_LIBINPUT` is **not** set — libinput is active and provides correct touch event delivery. The double-tap prevention is in `InputDevice.cpp`, not the environment.

---

## 18. Phone Disconnect — Return to Splash

### 18.1 Problem: No Single Reliable Disconnect Signal

When the phone ends an AA session, `autoapp` does **not** exit — it stays running showing its own waiting screen. No single signal reliably covers all disconnect scenarios:

- **In-app AA disconnect** (phone stays BT+WiFi connected): no stdout pattern, no process exit, no BT disconnect, phone stays on AP — only TCP session drop and subsequent RFCOMM retry are detectable.
- **Phone reboot / BT settings disconnect**: BT Connected=False fires, but may be delayed by BT timeout (minutes).
- **Range loss**: phone eventually leaves the WiFi AP (ARP table clears).

### 18.2 Fix: Multiple Independent Disconnect Signals

`_handle_projection_active()` races nine asyncio tasks; whichever fires first causes the transition to IDLE:

**Primary signals (fastest):**

1. **`wait_for_tcp_session_end()`** (`openauto.py`): polls `ss -tn src :5000` every 3 s. Fires within ~3 s when autoapp's TCP session to the phone drops. This is the fastest and most reliable signal for in-app AA disconnects.

2. **`wait_for_rfcomm_reconnect_attempt()`** (`ble.py`): blocks on the RFCOMM connection queue. When the user disconnects AA in-app, Android immediately starts retrying RFCOMM connections (~every 13 s). The first new RFCOMM fd is closed and the task returns — IDLE will consume the next one.

3. **`wait_for_client_leave_ap()`** (`wifi.py`): polls the `uap0` ARP/neighbour table every 3 s. First confirms the client IS present (avoids false-positive on startup), then fires when no clients remain. Covers range loss and BT-settings disconnect.

4. **`wait_for_phone_disconnect()`** (`ble.py`): subscribes to `org.freedesktop.DBus.Properties` signals on the phone's BlueZ Device1 object. Fires when `Connected` goes `False`. Slowest (BT timeout can be minutes) but catches hard disconnects.

**Fallback signals:**

5. **`wait_for_projection_stopped()`** (`openauto.py`): streams autoapp stdout/stderr for known patterns (`onAndroidAutoQuit`, `[WifiProjectionService] stop()`, etc.).

6. **`wait_for_exit()`** (`openauto.py`): autoapp process exit (exit code 0 = clean disconnect, non-zero → ERROR_RECOVERY).

7. **`ignition_task`**: GPIO 17 ignition-off → SHUTDOWN.

8. **`clock_task`**: `_periodic_clock_save()` — loops forever, never fires first.

9. **`bluez_task`** (`ble.py`): `watch_bluez_restart()` — fires if bluetoothd crashes or restarts. See §22.

### 18.3 IDLE Reconnect Retry Loop

On entering IDLE, a `_reconnect_loop` background task calls `try_reconnect_phone()` immediately, then every 30 s:

```python
async def _reconnect_loop(mac: str) -> None:
    while True:
        await self._ble.try_reconnect_phone(mac)
        await asyncio.sleep(30)
asyncio.create_task(_reconnect_loop(last_mac))
```

This ensures the Pi pages the phone as soon as it comes back into BT range (e.g. after a phone reboot), rather than relying solely on the phone picking up the BLE advertisement. The task is cancelled automatically when `phone_task` fires and `asyncio.wait` returns.

---

## 20. Phone Auto-Reconnect on Boot (OEM-Style)

### 20.1 Problem

PiAuto registers a WAA RFCOMM **server** profile — it waits for the phone to initiate the RFCOMM connection. Android does not auto-connect to SPP/RFCOMM profiles the way it auto-connects audio profiles (HFP, A2DP). As a result, the phone did not connect automatically on Pi boot; the user had to manually open BT settings and tap "Connect".

### 20.2 How OEM Head Units Work

OEM head units register as an **HFP Hands-Free (HF)** device (Bluetooth UUID 0x111e). On power-on they page the phone (Classic BT ACL) and connect HFP. Android detects the HFP HF device and immediately enters **car mode** — this triggers Android Auto to initiate the WAA RFCOMM session to the head unit's registered WAA profile without any user interaction. The user sees Android Auto launch on their phone in seconds, even with the screen off.

The key insight is that Android's car-mode detection fires on **HFP HF connection**, not on BLE scan. BLE advertising for WAA discovery is only the initial pairing mechanism; subsequent auto-reconnect uses HFP.

### 20.3 Fix: `BleManager.try_reconnect_phone()` with HFP HF

After `start_advertising()` in IDLE, the state machine fires a background task:

```python
last_mac = self._ble.get_last_connected_mac()
if last_mac:
    asyncio.create_task(self._ble.try_reconnect_phone(last_mac))
```

`try_reconnect_phone()` performs a two-step sequence:

**Step 1 — Establish ACL + A2DP:** call BlueZ `Device1.Connect()` on the last known phone's device path. This pages the phone and connects internally-handled profiles (primarily A2DP via WirePlumber).

```python
await asyncio.wait_for(dev_iface.call_connect(), timeout=15.0)
```

**Step 2 — Register HFP HF profile AFTER connect:** immediately after `Device1.Connect()` returns, register an HFP HF `Profile1` with BlueZ:

```python
if not self._hfp_hf_registered:
    await self._register_hfp_hf_profile()
```

The registration options: `Role="client"`, `AutoConnect=True`, `Version=0x0108` (HFP 1.8), `Features=0x0000` (minimal).

**Critical timing:** BlueZ's `AutoConnect=True` for a `Profile1` fires when the profile is **registered while the device is already connected** — not when the device connects to an already-registered profile. By registering after `Device1.Connect()`, the HFP HF channel connects immediately. Android detects the HFP HF device and fires car mode, which triggers the WAA RFCOMM back to the Pi.

**Step 3 — HFP SLC:** `Profile1.NewConnection` receives the RFCOMM socket fd. A daemon thread runs the minimal HFP Service Level Connection (SLC) handshake: `AT+BRSF=0` → `AT+CIND=?` → `AT+CIND?` → `AT+CMER=3,0,0,1`. The thread then holds the socket open (keeping HFP connected) until Android Auto ends the session.

The WAA RFCOMM `Profile1.NewConnection` fires concurrently, `wait_for_phone()` picks up the fd from its queue, and the normal IDLE → BT_PAIRING transition proceeds.

**Observed latency:** service start → HFP connected → RFCOMM received = ~1 second on cold boot.

### 20.4 Trusted Flag

Phones are set `Trusted=True` in BlueZ when first paired (`bt_pair.py` already does this for speakers; `statemachine.py` calls `ble.trust_device(mac)` after saving the phone pairing). This accelerates BlueZ's reconnect policy and is required for `AutoConnect=True` to fire reliably.

### 20.5 Failure Handling

- Phone not in range: `call_connect()` times out after 15 s. Logged at DEBUG level. BLE advertising continues as fallback.
- Phone not in BlueZ cache (never paired): device path introspection fails. Logged at DEBUG. No action.
- HFP registration fails: exception logged at DEBUG. `_hfp_hf_registered` remains `False` so the next reconnect attempt will retry.
- No previously paired phone: `get_last_connected_mac()` returns `None`. Task not created.

The reconnect task runs concurrently with `asyncio.wait` for phone/ignition/setup events. A failure does not affect normal operation — BLE advertising remains active as the discovery fallback.

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

---

## 21. BlueZ 5.82 SEGV on Multi-Profile Device Connect

### 21.1 Symptom

Pairing a device that advertises multiple Bluetooth profiles simultaneously (e.g. Logi Dock: HFP + A2DP) causes `bluetoothd` to crash with SIGSEGV:

```
bluetooth.service: Main process exited, code=killed, status=11/SEGV
bluetooth.service: Failed with result 'signal'.
bluetooth.service: Scheduled restart job immediately on client request
```

From `bt_pair`'s perspective the error is:
```
PAIR_FAIL|Connect failed: Message recipient disconnected from message bus without replying
```

The "recipient disconnected" is bluetoothd crashing before it can reply to the D-Bus `Connect()` call.

### 21.2 Root Cause

The crash occurs inside BlueZ's AVDTP (A2DP) codec negotiation when `bt_pair` calls `Device1.Connect()` at the same moment WirePlumber is registering its A2DP media endpoints. Both operations touch BlueZ's internal profile state machine concurrently, hitting a null-pointer dereference in bluetoothd 5.82.

The race only manifests on devices with multiple profiles because:
- Simple A2DP-only speakers: BlueZ negotiates only one profile — no race window
- HFP + A2DP devices: BlueZ tries to negotiate both profiles simultaneously, widening the window where WirePlumber's endpoint registration collides with the connect path

### 21.3 Fix

`bt_pair` no longer calls `Device1.Connect()`. See §9.4 for the full rationale and implementation.

WirePlumber is the authoritative owner of audio device connections. Its `bluez5.auto-connect = [ a2dp_sink ]` configuration (in `~/.config/wireplumber/wireplumber.conf.d/50-bluez-config.conf`) causes it to connect the A2DP sink profile automatically after a device is paired and trusted — without any explicit `Connect()` call from application code.

### 21.4 Affected Versions / Devices

- **BlueZ version:** 5.82 (Debian 13 / Trixie package)
- **Confirmed affected:** Logi Dock (HFP + A2DP)
- **Not affected:** simple A2DP-only speakers (e.g. standard Bluetooth audio receivers)
- **Workaround if issue recurs:** remove the device from BlueZ (`bluetoothctl remove MAC`), reboot, then re-pair via the Setup UI

---

## 22. BlueZ Crash Watchdog

### 22.1 Problem

When `bluetoothd` crashes and systemd restarts it, all piauto D-Bus registrations are silently lost:

- The RFCOMM WAA profile registration (`Profile1`) is gone — the phone can no longer connect.
- The HFP HF profile registration is gone — auto-reconnect on the next phone entry stops working.
- BlueZ's internal pairing agent is unregistered — new pairings will fail silently.

piauto had no way to detect this: it continued running as if nothing happened, but the BT stack was in a broken state that required a manual service restart.

This is not a theoretical edge case. `bluetoothd` 5.82 crashes on AVDTP race conditions (see §21) and may crash on other unexpected device behaviours. A consumer product must self-heal.

### 22.2 Detection Mechanism

`BleManager.watch_bluez_restart()` (`ble.py`) uses two complementary methods:

**Primary — D-Bus `NameOwnerChanged` signal:**

`org.freedesktop.DBus` fires `NameOwnerChanged(name, old_owner, new_owner)` whenever a service acquires or loses its D-Bus name. When `bluetoothd` crashes, `org.bluez` loses its name owner — `old_owner` is non-empty and `new_owner` is empty. This signal fires within milliseconds of the crash.

```python
dbus_intro = await self._bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus")
dbus_obj = self._bus.get_proxy_object("org.freedesktop.DBus", "/org/freedesktop/DBus", dbus_intro)
dbus_iface = dbus_obj.get_interface("org.freedesktop.DBus")

def _on_name_owner_changed(name, old_owner, new_owner):
    if name == BLUEZ_SERVICE and old_owner:
        restarted.set()

dbus_iface.on_name_owner_changed(_on_name_owner_changed)
```

**Fallback — health-poll every 5 s:**

If the signal subscription fails (e.g. D-Bus policy restriction), a background coroutine introspects `/org/bluez` every 5 seconds. A failed introspect (timeout or error) sets the same `restarted` event.

```python
async def _health_poll():
    while not restarted.is_set():
        await asyncio.sleep(5)
        try:
            await asyncio.wait_for(
                self._bus.introspect(BLUEZ_SERVICE, "/org/bluez"), timeout=3.0
            )
        except Exception:
            restarted.set()
```

Both paths set a shared `asyncio.Event`. `watch_bluez_restart()` awaits this event and returns when either path fires. The health-poll task is cancelled and the signal subscription is torn down when the event is set.

### 22.3 Response

When `watch_bluez_restart()` returns, the state machine logs a `CRITICAL` message, performs any necessary cleanup (kills OpenAuto if in PROJECTION_ACTIVE), and calls `sys.exit(1)`:

```
piauto.ble [WARNING] BlueZ name owner changed (:1.26 → none) — RFCOMM/HFP profile registrations lost
piauto.statemachine [CRITICAL] BlueZ crashed/restarted — all profile registrations lost; exiting...
```

`piauto.service` has `Restart=on-failure` and `RestartSec=2`. Systemd restarts piauto 2 seconds after exit, which re-runs the full `BOOTING` sequence: re-registers the RFCOMM profile, HFP profile, and pairing agent with the now-fresh BlueZ instance.

**Recovery timeline (observed):**
- T+0: BlueZ killed (`SIGKILL`)
- T+0: `NameOwnerChanged` fires; piauto logs CRITICAL, kills OpenAuto, exits
- T+2s: systemd restarts piauto
- T+4s: piauto in IDLE, all profiles re-registered, BLE advertising active, auto-reconnect loop running

### 22.4 Why `sys.exit(1)` Instead of Re-registering In-Place

Re-registering profiles in-place after a BlueZ restart is theoretically possible but fragile:

- The existing D-Bus `MessageBus` connection is still valid (it's connected to the system D-Bus, not to BlueZ directly), but all proxy objects obtained before the crash are stale.
- Every `BleManager` object that holds a proxy reference (`_adapter`, `_agent_mgr`, etc.) must be rebuilt from scratch.
- `WifiManager`, `OpenAutoManager`, and other subsystems that made D-Bus calls may also hold stale references.
- The state machine could be in any state (IDLE, BT_PAIRING, WIFI_WAIT, TCP_CONNECT, PROJECTION_ACTIVE) and would need per-state recovery logic.

A clean process restart via systemd is simpler, guaranteed correct (Python starts with a clean heap), and fast enough (2s `RestartSec`) to be invisible to the user.

### 22.5 Integration in State Machine

`watch_bluez_restart()` runs as a concurrent task in **both** IDLE and PROJECTION_ACTIVE:

**IDLE:**
```python
phone_task   = asyncio.create_task(self._ble.wait_for_phone())
ignition_task = asyncio.create_task(self._ignition_off.wait())
setup_task   = asyncio.create_task(_watch_splash_stdout())
bluez_task   = asyncio.create_task(self._ble.watch_bluez_restart())

done, _ = await asyncio.wait([phone_task, ignition_task, setup_task, bluez_task],
                              return_when=asyncio.FIRST_COMPLETED)
if bluez_task in done:
    sys.exit(1)
```

**PROJECTION_ACTIVE:** `bluez_task` is the ninth task in the 9-task wait (§18.2). On fire: kill OpenAuto, then `sys.exit(1)`.

States that do not block on a long-running `asyncio.wait` (BT_PAIRING, WIFI_WAIT, TCP_CONNECT, ERROR_RECOVERY) use bounded timeouts — a BlueZ crash in these states will surface as a BlueZ D-Bus error on the next call, which is caught and transitions to IDLE or SHUTDOWN normally. Adding the watchdog to these short-lived states would add complexity with minimal benefit.

---

## 23. Consumer-Grade Reliability Hardening

Code review (2026-04-11) identified five reliability gaps that could cause silent failures or stray coroutines in a car environment. All are fixed in the same commit.

### 23.1 PROJECTION_ACTIVE Pending Tasks Not Awaited

**Problem:** After `asyncio.wait()` returns in `_handle_projection_active()`, cancelled tasks were not awaited. The pattern `for task in pending: task.cancel()` requests cancellation but does not wait for it to complete. Coroutines holding D-Bus subscriptions or subprocess handles could continue executing into the next state handler.

**Fix:** Added `await asyncio.gather(*pending, return_exceptions=True)` immediately after the cancel loop, matching the pattern already used correctly in `_handle_idle()`.

### 23.2 OpenAuto Monitor Task Crash Leaves Projection Undetectable

**Problem:** `_monitor_output()` and `_monitor_stdout()` are the two background tasks that parse OpenAuto's output to set `_projection_active` and `_projection_stopped` events. Both have exception handlers that log and return — meaning if both tasks exit early (e.g. due to a subprocess pipe error), neither event is ever set. `wait_for_ready()` would then silently time out after 30 s rather than detecting the failure immediately.

**Fix:** A `_monitor_guard()` coroutine is created alongside the two monitor tasks. It awaits both via `asyncio.gather(return_exceptions=True)`. If both exit before `_projection_active` is set, it sets `_projection_stopped` immediately. This ensures `wait_for_projection_stopped()` (running concurrently in PROJECTION_ACTIVE) fires quickly, causing a clean transition to IDLE rather than a silent 30 s wait.

```python
async def _monitor_guard() -> None:
    await asyncio.gather(
        self._monitor_task, self._stdout_task,
        return_exceptions=True,
    )
    if not self._projection_active.is_set():
        log.warning("Both OpenAuto monitor tasks exited before projection was active")
        self._projection_stopped.set()

asyncio.create_task(_monitor_guard())
```

### 23.3 Thermal Sensor Persistent Failure Silent After First Log

**Problem:** If the thermal sensor (`/sys/class/thermal/thermal_zone0/temp`) becomes permanently unreadable, the monitor logs one warning at startup and then silently skips all fan control indefinitely. The fan holds its last duty cycle with no further indication that temperature monitoring has stopped.

**Fix:** The polling loop now tracks consecutive read failures and logs a warning on the first failure and every 12th subsequent failure (~1 minute at 5 s poll interval). A recovery message is logged when the sensor becomes readable again.

### 23.4 wpctl Subprocess Orphaned on CancelledError

**Problem:** `_set_pipewire_volume()` in `volume.py` spawns a `wpctl set-volume` subprocess and awaits `proc.wait()`. If the enclosing task is cancelled while awaiting, the wpctl process is left running without cleanup. In practice wpctl finishes in under 50 ms, but over many volume changes accumulated orphan processes could exhaust system resources.

**Fix:** The `await self._wpctl_proc.wait()` call is now wrapped in a try/except that kills the process and re-raises `CancelledError`:

```python
try:
    await self._wpctl_proc.wait()
except asyncio.CancelledError:
    self._wpctl_proc.kill()
    await self._wpctl_proc.wait()
    raise
```

### 23.5 IDLE Reconnect Loop Exception Lost Without Context

**Problem:** The `_reconnect_loop` background task in `_handle_idle()` called `try_reconnect_phone()` with no exception handler. `try_reconnect_phone()` has its own internal exception handler, but if it ever raised unexpectedly (e.g. an asyncio internal error), the loop would crash silently — visible only in the asyncio unhandled-exception logger, not the piauto journal.

**Fix:** Added explicit exception handling in `_reconnect_loop`:

```python
async def _reconnect_loop(mac: str) -> None:
    while True:
        try:
            await self._ble.try_reconnect_phone(mac)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Reconnect loop error for %s — will retry in 30 s", mac)
        await asyncio.sleep(30)
```

`CancelledError` is re-raised so the task cancels cleanly when IDLE exits.

---

## 24. v0.2.0 Reliability Hardening

This section documents the 17 fixes applied in v0.2.0. They are grouped by phase. All 53 automated tests pass after each phase.

### Phase 1 — Critical/High Reliability Fixes

#### 24.1 BLE Adapter Path Hardcoding (`ble.py`)

**Problem:** `BleManager` hardcoded `/org/bluez/hci0` throughout. When a USB BT dongle is present as `hci1`, the on-board adapter moves to `hci0` — or vice versa depending on probe order. Any adapter index mismatch causes all BLE/RFCOMM/HFP operations to fail silently.

**Fix:** Added `BleManager._detect_adapter()` which queries BlueZ's `ObjectManager.GetManagedObjects()` at `setup()` time to find the first powered adapter. The discovered path is stored as `self._adapter_path` and used everywhere in place of the literal string. Defaults to `/org/bluez/hci0` if detection fails (logged as a warning).

#### 24.2 ARP False-Positive on Client Leave (`wifi.py`)

**Problem:** `wait_for_client_leave_ap()` checked the ARP table once and declared the client gone on any negative result. A single stale ARP entry or a brief kernel cache miss could return to splash prematurely.

**Fix:** Added a `consecutive_misses` counter — the client is only considered gone after **2 consecutive** negative ARP checks (polling every 1 s). A single miss resets the counter.

#### 24.3 HFP SLC Thread Hanging (`ble.py`)

**Problem:** The HFP SLC socket was set to `sock.settimeout(None)` (blocking) after the handshake. The background thread then called `sock.recv(512)` in an infinite loop. If the remote closed the connection without sending data, the call would block indefinitely, leaking the thread.

**Fix:** Retained the 5-second socket timeout from the handshake phase. The keepalive loop handles `socket.timeout` as a normal idle condition (`continue`) rather than an error. A clean EOF or a non-timeout exception breaks the loop and closes the socket.

#### 24.4 Splash Stdout Race (`splash.py`, `statemachine.py`)

**Problem:** `SplashManager.read_stdout_line()` used a boolean flag `_stdout_reading` and polled with `asyncio.sleep(0.1)`. Any line produced while the flag was `True` (another caller active) was permanently lost. A "BT_SETUP" line dropped this way would leave the state machine deaf to the Setup button press.

**Fix:** Replaced the flag/poll pattern with a dedicated `_pump_stdout()` background task that runs a continuous `readline()` loop and feeds each line into `asyncio.Queue`. `read_stdout_line()` does `await self._line_queue.get()`, which blocks until a line is available. No line can be dropped.

#### 24.5 OpenAuto Port TIME_WAIT (`openauto.py`)

**Problem:** After `pkill autoapp`, the TCP port 5000 sometimes stayed in TIME_WAIT for up to 60 seconds. If OpenAuto was immediately relaunched (e.g. on quick phone reconnect), it would fail to bind the port and exit with an error.

**Fix:** `_kill_stale_autoapp()` now polls `ss -tlnH` after pkill, waiting up to 10 seconds for `:5000` to disappear from the socket table. It logs a warning if the port is still occupied after 10 seconds, giving the operator visibility without blocking indefinitely.

#### 24.6 Splash DRM Handoff Race (`splash.py`)

**Problem:** The state machine called `self._splash.kill()` and then immediately launched OpenAuto. If the splash Qt process hadn't fully released DRM master yet, OpenAuto would fail to acquire it and exit with a framebuffer error. The symptom was `WARNING: Splash process did not stop in X s`.

**Fix:** `SplashManager.kill()` now sends a `"QUIT\n"` command via the splash process's stdin before sending SIGTERM. The Qt application handles `"QUIT"` in its stdin handler by calling the SIGTERM handler directly — this is a faster path than the kernel signal delivery and ensures the Qt event loop exits cleanly and DRM master is released before `kill()` returns. SIGTERM (3 s timeout) and SIGKILL are retained as fallbacks.

#### 24.7 Ignition Callback Not Idempotent (`statemachine.py`)

**Problem:** The ignition-off GPIO callback `_on_ignition_off()` could fire multiple times (GPIO bounce, multiple edge detections) and trigger multiple shutdown sequences. Duplicate `shutdown -h now` calls are harmless but duplicate state transitions (e.g. double-entering SHUTDOWN) could cause log noise or unexpected behavior.

**Fix:** Added a `self._ignition_fired: bool = False` guard. `_on_ignition_off()` returns immediately if `_ignition_fired` is already `True`; sets it to `True` before proceeding.

---

### Phase 2 — Audio/WiFi Reliability Fixes

#### 24.8 WiFi Channel Enforcement in AP+STA Mode (`wifi.py`)

**Problem:** In AP+STA mode, `WifiManager.start_ap()` wrote the channel from `piauto.yaml` into the hostapd config. If the STA connected on a different channel (e.g. the router switched from 149 to 36), the AP config would be wrong, causing the AP to fail to start or phones to be unable to connect.

**Fix:** Added `_detect_sta_channel(sta_iface)` which runs `iw dev wlan0 info` and parses the `channel` line. `start_ap()` calls this in AP+STA mode and overrides `self._effective_channel` with the actual STA channel before writing configs.

#### 24.9 Volume Sync D-Bus Hang (`volume.py`)

**Problem:** `VolumeMonitor._get_transports(bus)` could hang indefinitely if BlueZ was unresponsive or the transport object path was stale. This blocked the `volume_sync` task, preventing it from ever polling volume again.

**Fix:** Wrapped the `_get_transports()` call in `asyncio.wait_for(..., timeout=3.0)`. On `asyncio.TimeoutError`, the D-Bus bus connection is disconnected and a fresh one is reconnected (also with a 3 s timeout) before the polling loop continues.

#### 24.10 MAC Address Case Inconsistency (`ble.py`, `config.py`)

**Problem:** BlueZ D-Bus returns MAC addresses in uppercase (`AA:BB:CC:DD:EE:FF`), but some paths (user input, RFCOMM extraction) could produce lowercase MACs. Comparisons between differently-cased MACs failed, causing valid paired devices to be unrecognised.

**Fix:** All MAC addresses are uppercased at every entry point: `PairingStore.save_pairing()`, `_extract_device_info()`, and `config.save_speaker_mac()`. The pairing store's `load()` and `save_pairing()` methods also sort records by `(-last_connected, mac)` for deterministic ordering.

#### 24.11 Configurable DHCP Range (`config.py`, `wifi.py`)

**Problem:** The standalone-mode DHCP range (192.168.1.100–199) was hardcoded, making it impossible to deploy PiAuto on a network segment where that range conflicts with existing DHCP leases.

**Fix:** Added optional `dhcp_start` and `dhcp_end` fields to `WifiConfig`. `WifiManager` uses these if set, falling back to the built-in defaults. The fields are ignored in AP+STA mode (NetworkManager handles DHCP).

---

### Phase 3 — Pre-Release Polish

#### 24.12 Config Validation for Critical Fields (`config.py`)

**Problem:** A config file with `openauto.binary: ""` (empty string) or an out-of-range `power.ignition_debounce_ms` (e.g. 0 or 100000) would pass validation and cause a confusing runtime failure rather than a clear startup error.

**Fix:** Added validation rules in `_validate()`:
- `power.ignition_debounce_ms` must be in range 1–5000 ms.
- `openauto.binary` must not be empty.

Violations are collected and logged as warnings at startup (consistent with the existing validation approach — PiAuto boots with defaults rather than refusing to start).

#### 24.13 MAC Validation in `save_speaker_mac` (`config.py`)

**Problem:** `save_speaker_mac()` accepted any string as a MAC address. A malformed value (e.g. empty string, partial address, or non-hex characters) would be written to the config file and then cause failures when BlueZ tried to look up the device.

**Fix:** Added a regex check against `_MAC_RE = re.compile(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$')` (after uppercasing). Invalid MACs are rejected with a `log.warning` and the config is not written.

#### 24.14 Pairing Store Deterministic Ordering (`ble.py`)

**Problem:** `PairingStore.load()` returned records in file-storage order. Across writes and reloads, the order was non-deterministic, making the "most recently connected device" ordering unreliable for the auto-reconnect selection logic.

**Fix:** `load()` sorts records by `(-last_connected, mac)` — most-recent first, with MAC as a tiebreaker for determinism. `save_pairing()` applies the same sort before writing, so the file is always in a predictable order.

#### 24.15 Splash Subprocess Environment Restriction (`splash.py`)

**Problem:** `SplashManager._build_env()` passed the full `os.environ` to the splash subprocess. This exposed sensitive environment variables (e.g. `PIAUTO_CONFIG_PATH` pointing to a secrets file, shell history paths) to the Qt process, which runs with a broader attack surface (IPC sockets, Qt plugins).

**Fix:** Replaced `**os.environ` with an explicit whitelist. The splash subprocess receives only: `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `XDG_*`, `LD_*`, `PIAUTO_*`, and Qt EGLFS display variables. All other environment variables are excluded.

#### 24.16 Type Hint Completeness (`bt_pair.py`)

**Problem:** `PairingAgent.__init__` was missing a `-> None` return type annotation, inconsistent with the rest of the codebase's type-hint coverage.

**Fix:** Added `-> None` to `PairingAgent.__init__`.

#### 24.17 Test Suite Expansion (53 tests)

Added `tests/test_53.py` covering:
- Config validation (`_validate()`) — ignition_debounce_ms bounds, empty binary, MAC regex
- `BleManager.setup()` — adapter detection fallback, ObjectManager failure handling
- State machine transitions — BOOTING→IDLE, IDLE→BT_PAIRING, PROJECTION_ACTIVE→IDLE disconnect paths
- `PairingStore` ordering — deterministic sort after mixed-timestamp records

All 53 tests pass on a development machine without Pi hardware (hardware-dependent modules mock gracefully).
