# Raspberry Pi Setup Guide: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-PSG-001               |
| Version        | 1.1                          |
| Date           | 2026-03-29                   |
| Status         | Draft                        |

## 1. Prerequisites

### 1.1 Hardware (per PiAuto-HW-001)

| Item | Specification |
| :--- | :------------ |
| Raspberry Pi | Model 4B, 4 GB RAM |
| SD Card | 16 GB minimum, high-endurance (Samsung PRO Endurance or SanDisk Max Endurance) |
| Display | LCDWiki 7" HDMI Display-B (800x480, USB capacitive touch) |
| Power Supply | 5.1 V / 3 A via USB-C (bench) or vehicle buck converter (installed) |
| Fan | 5 V or 12 V DC fan with MOSFET driver circuit on GPIO 4 |
| Bluetooth Speaker | A2DP-capable speaker or audio receiver for audio output |

### 1.2 Connections

| Pi Port | Cable | Destination |
| :------ | :---- | :---------- |
| Micro-HDMI 0 | Micro-HDMI → HDMI | Display HDMI input |
| USB 2.0 | USB-A → Micro-USB | Display (touch input + display power) |
| GPIO 4 (pin 7) | Wire → 10 kΩ → MOSFET gate | Fan control circuit |
| GPIO 17 (pin 11) | Wire (with pull-up) | Ignition sense (LOW = off) |

### 1.3 Development Machine

You'll also need a laptop or desktop with:
- SD card reader (or USB adapter)
- SSH client
- Raspberry Pi Imager (or `dd`)

---

## 2. Base OS Installation

### 2.1 Flash the Image

1. Download **Raspberry Pi OS Lite (64-bit, Trixie)** from https://www.raspberrypi.com/software/operating-systems/
2. Flash to SD card using Raspberry Pi Imager.
3. In Imager's advanced settings:
   - Enable SSH (password or key-based)
   - Set hostname: `piauto`
   - Set username/password for initial login (e.g., `pi` / your password)
   - Configure Wi-Fi if needed for initial setup (this is temporary — the Pi will be its own AP in production)

### 2.2 First Boot

1. Insert SD card, connect display and keyboard (or use SSH).
2. Boot the Pi and log in.
3. Run initial updates:

```bash
sudo apt update && sudo apt upgrade -y
```

---

## 3. System Package Installation

### 3.1 Runtime Packages

```bash
sudo apt install -y \
    bluez \
    hostapd \
    dnsmasq \
    iw \
    pipewire \
    wireplumber \
    pipewire-audio-client-libraries \
    libgpiod-dev \
    python3-gpiod \
    python3-pip \
    python3-venv \
    python3-yaml \
    python3-dbus \
    openssl \
    fake-hwclock \
    python3-pyqt5 \
    libqt5multimedia5 \
    libqt5multimedia5-plugins \
    libqt5multimediawidgets5
```

### 3.2 Build Dependencies (for OpenAuto/aasdk)

```bash
sudo apt install -y \
    cmake \
    build-essential \
    git \
    protobuf-compiler \
    libprotobuf-dev \
    libssl-dev \
    libboost-all-dev \
    libusb-1.0-0-dev \
    qtmultimedia5-dev \
    qtconnectivity5-dev \
    libqt5websockets5-dev \
    libqt5svg5-dev \
    librtaudio-dev \
    libtag-dev \
    libgps-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav
```

> **Note:** The GStreamer dev packages are required for the `GSTVideoOutput` rewrite (see Implementation Guide §2.4 patch #4). These replace the removed qt-gstreamer (QGlib) dependency.

---

## 4. Build OpenAuto and aasdk

### 4.1 Build aasdk

Use the `AndrewGraydon/aasdk` fork on branch `piauto-debian13`. This includes the OpenSSL 3.x compatibility patch required for Debian 13 (Trixie).

```bash
cd /tmp
git clone -b piauto-debian13 https://github.com/AndrewGraydon/aasdk.git
cd aasdk

mkdir build && cd build
sudo cmake -DCMAKE_BUILD_TYPE=Release \
    -DSKIP_BUILD_PROTOBUF=ON \
    -DSKIP_BUILD_ABSL=ON \
    ..
sudo make -j3
sudo make install
sudo ldconfig
```

> **Note:** `-DSKIP_BUILD_PROTOBUF=ON -DSKIP_BUILD_ABSL=ON` uses the system protobuf (3.21) instead of downloading protobuf v30 + abseil, which avoids build conflicts on Trixie.

> **Note:** Use `-j3` on Pi 4 to avoid thermal throttling during the build. The `AndrewGraydon/aasdk` patch wraps deprecated OpenSSL 1.x calls in version guards so the build succeeds against OpenSSL 3.x on Debian 13.

### 4.2 Build OpenAuto

Use the `AndrewGraydon/openauto` fork on branch `piauto-debian13`. This includes the GSTVideoOutput rewrite, RtAudio 6.x fix, RtAudio mutex (audio stutter fix), and removal of qt-gstreamer dependency.

```bash
# Clone to /opt/openauto so RPATH is correct
sudo git clone -b piauto-debian13 https://github.com/AndrewGraydon/openauto.git /opt/openauto

# Configure (note: -DGST_BUILD=ON not TRUE)
cmake -S /opt/openauto -B /opt/openauto/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGST_BUILD=ON \
    -DNOPI=ON

# Build (-j2 avoids thermal throttle on Pi 4; use -j3 if cooled)
cmake --build /opt/openauto/build -j2

# Install binary to production path
sudo cp /opt/openauto/bin/autoapp /usr/local/bin/autoapp
```

> **Note:** `-DGST_BUILD=ON` enables the GStreamer video path (required for correct H.264 rendering on EGLFS). The `GSTVideoOutput` implementation in this fork uses the plain GStreamer C API — no qt-gstreamer dependency. Do NOT omit this flag as it falls back to `QtVideoOutput` (QMediaPlayer + QVideoWidget) which cannot render raw H.264 NAL units on EGLFS.

> **Note:** `-DNOPI=ON` disables the Broadcom OMX/VideoCore (`bcm_host.h`) path.

> **Note:** The binary is built to `/opt/openauto/bin/autoapp` and the shared library to `/opt/openauto/lib/libopenauto.so.2`. The RPATH in the binary points to `/opt/openauto/lib/`, so `libopenauto.so.2` is found at runtime without needing `ldconfig` or copying the library elsewhere.

> **Note:** The `opencardev` binary previously used is saved at `/usr/local/bin/autoapp-2026.03.28+git.4cc739b` as a rollback. See Implementation Guide §2.4 for full migration details.

#### Rebuilding after source changes

```bash
cmake -S /opt/openauto -B /opt/openauto/build \
    -DCMAKE_BUILD_TYPE=Release -DGST_BUILD=ON \
    && cmake --build /opt/openauto/build -j2
sudo cp /opt/openauto/bin/autoapp /usr/local/bin/autoapp
sudo systemctl restart piauto
```

### 4.3 Create OpenAuto Configuration

```bash
sudo tee /data/openauto.ini << 'EOF'
[General]
ShowCursor=false
HideWarning=true

[Video]
FPS=1
Resolution=1
ScreenDPI=140

[AudioChannel]
MediaEnabled=true
GuidanceEnabled=true
SystemEnabled=true
TelephonyEnabled=true

[Audio]
OutputBackendType=1

[Bluetooth]
WirelessProjectionEnabled=true

[Input]
EnableTouchscreen=true
EOF
```

> Video Resolution=1 is 800x480 (the AA protocol maximum at 30 FPS). The display native resolution is 1024x600; Qt EGLFS renders the 800x480 frame and the display scales it.

### 4.4 Record Build Info

```bash
echo "aasdk: $(cd /opt/aasdk && sudo git rev-parse HEAD)" | sudo tee /data/build-info.txt
echo "openauto: $(cd /opt/openauto && sudo git rev-parse HEAD)" | sudo tee -a /data/build-info.txt
echo "build-date: $(date -Is)" | sudo tee -a /data/build-info.txt
```

---

## 5. Boot Configuration

### 5.1 Display and GPU — `/boot/firmware/config.txt`

> **WARNING:** On RPi OS Trixie with the KMS display driver (`dtoverlay=vc4-kms-v3d`), do NOT add legacy display settings (`hdmi_group`, `hdmi_mode`, `hdmi_cvt`, `hdmi_drive`). These conflict with KMS and will cause a blank screen on boot.

The KMS driver auto-detects the display resolution. No display-specific config.txt changes are needed. Optionally add:

```ini
# GPU memory for V4L2 H.264 decode + Qt EGLFS
gpu_mem=128

# SD card overclock for faster boot
dtparam=sd_overclock=100
```

### 5.2 Kernel Parameters — `/boot/firmware/cmdline.txt`

Append to the existing line (do NOT add a newline):

```
quiet loglevel=0 initial_turbo=30
```

---

## 6. Partition and Filesystem Setup

### 6.1 Create the `/data` Partition

The writable `/data` partition stores configuration, pairing records, TLS certificates, and the clock file.

```bash
# Check current partition layout
sudo fdisk -l /dev/mmcblk0

# If space is available after the root partition, create partition 3:
sudo fdisk /dev/mmcblk0
# n → p → 3 → (default start) → +1G → w

# Format and mount
sudo mkfs.ext4 -L pidata /dev/mmcblk0p3
sudo mkdir -p /data
sudo mount /dev/mmcblk0p3 /data
```

Add to `/etc/fstab`:

```
/dev/mmcblk0p3  /data  ext4  defaults,noatime  0  2
```

### 6.2 Create Directory Structure on `/data`

```bash
sudo mkdir -p /data/bt        # BLE pairing records
sudo mkdir -p /data/tls       # TLS certificates (generated on first boot)
```

### 6.3 Read-Only Root Filesystem (overlayfs)

> **Important:** Configure this LAST, after all other setup is complete. Once enabled, changes to `/` are volatile.

```bash
sudo raspi-config
# → Performance Options → Overlay File System → Enable
# → Reboot when prompted
```

Or manually:

```bash
sudo apt install -y overlayroot
# Edit /etc/overlayroot.conf:
#   overlayroot="tmpfs"
```

After enabling overlayfs:
- `/` is read-only (backed by ext4, overlaid with tmpfs)
- `/data` remains writable (separate mount)
- `/tmp`, `/var/log` are tmpfs (volatile)

To make changes to the root filesystem later, temporarily disable overlayfs via `raspi-config`.

---

## 7. User and Permission Setup

### 7.1 Create the `piauto` User

```bash
sudo useradd -r -s /usr/sbin/nologin -d /data piauto
sudo usermod -aG audio,video,bluetooth,input piauto
```

Groups:
- `audio` — PipeWire access
- `video` — DRM/KMS and V4L2 access (for OpenAuto)
- `bluetooth` — BlueZ access
- `input` — Touchscreen input device access

### 7.2 Data Directory Permissions

```bash
sudo chown -R piauto:piauto /data/bt
sudo chown -R piauto:piauto /data/tls
```

---

## 8. Service Configuration

### 8.1 Disable Unnecessary Services

Reduce boot time by disabling services not needed on a headless automotive system:

```bash
sudo systemctl disable --now \
    man-db.timer \
    apt-daily.timer \
    apt-daily-upgrade.timer \
    e2scrub_all.timer \
    fstrim.timer \
    ModemManager.service \
    avahi-daemon.service \
    triggerhappy.service
```

> **WARNING:** Do NOT disable `wpa_supplicant.service` — even though the Pi runs as an AP (hostapd) in production, disabling wpa_supplicant will kill any existing WiFi client connection (including SSH). Leave it enabled until the Pi has a dedicated AP-mode WiFi adapter or wired Ethernet.

### 8.2 Unblock Bluetooth and Enable Required Services

Bluetooth is soft-blocked by rfkill by default on RPi OS. Unblock it before enabling BlueZ:

```bash
sudo rfkill unblock bluetooth
sudo systemctl enable bluetooth.service
sudo systemctl enable pipewire.service
sudo systemctl enable wireplumber.service
```

### 8.3 Enable User Linger for PipeWire/WirePlumber

PipeWire and WirePlumber run as user services under the `pi` user. Without linger, logind kills the user slice when no active sessions exist (e.g., no SSH connections), tearing down A2DP endpoints and disconnecting Bluetooth speakers.

```bash
sudo loginctl enable-linger pi
```

### 8.4 Prevent hostapd and dnsmasq Auto-Start

These are started on-demand by the PiAuto state machine:

```bash
sudo systemctl disable hostapd.service
sudo systemctl disable dnsmasq.service
sudo systemctl mask hostapd.service
sudo systemctl mask dnsmasq.service
```

> The Python orchestrator runs hostapd/dnsmasq directly via subprocess, not through systemd.

### 8.5 Install the PiAuto Service

```bash
sudo cp /path/to/piauto.service /etc/systemd/system/piauto.service
sudo systemctl daemon-reload
sudo systemctl enable piauto.service
```

The service file (per PiAuto-IG-001 §5):

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

---

## 9. PiAuto Python Package Installation

### 9.1 Install the Package

```bash
cd /opt
sudo git clone <piauto-repo-url> piauto
cd piauto
sudo pip3 install --break-system-packages .
```

Or for development:

```bash
sudo pip3 install --break-system-packages -e ".[dev]"
```

### 9.2 Create Default Configuration

```bash
sudo tee /data/piauto.yaml << 'EOF'
wifi:
  ssid: "PiAuto"
  password: "changeme1"
  channel: 149
  country: "AU"

bluetooth:
  device_name: "PiAuto"
  max_paired: 8
  speaker_mac: ""

display:
  resolution: "1024x600"
  fps: 30

audio:
  output: "bluetooth"

thermal:
  fan_low_temp: 50
  fan_high_temp: 65
  hysteresis: 3
  poll_interval: 5

power:
  ignition_debounce_ms: 500
  shutdown_timeout_s: 10

openauto:
  binary: "/usr/local/bin/autoapp"
  extra_args: []
EOF
```

> **Edit `wifi.password` and `wifi.country`** before deploying. The password `changeme1` is a placeholder.

---

## 10. PipeWire and Bluetooth Audio Setup

### 10.1 PipeWire as Default Audio

PipeWire should be the default audio server on Trixie. Verify:

```bash
pactl info | grep "Server Name"
# Should show: PulseWire (PipeWire's PulseAudio compatibility layer)
```

### 10.2 WirePlumber Bluetooth Configuration

WirePlumber's bluez monitor checks for an active logind seat (`seat_state == "active"`) before starting. On a headless Pi, logind reports `seat_state = "online"` (no physical seat), so the bluez monitor never starts and A2DP endpoints are never registered.

Create `/home/pi/.config/wireplumber/wireplumber.conf.d/50-bluez-config.conf`:

```lua
monitor.bluez.properties = {
  bluez5.enable-sbc-xq = true
  bluez5.enable-msbc = true
  bluez5.enable-hw-volume = true
  bluez5.roles = [ a2dp_sink a2dp_source ]
}

monitor.bluez.rules = [
  {
    matches = [
      { device.name = "~bluez_card.*" }
    ]
    actions = {
      update-props = {
        bluez5.auto-connect = [ a2dp_sink ]
      }
    }
  }
]

wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

The critical setting is `monitor.bluez.seat-monitoring = disabled`, which makes the bluez monitor start unconditionally.

Restart WirePlumber after creating this file:

```bash
systemctl --user restart wireplumber
```

### 10.3 Pair the Bluetooth Speaker

The PiAuto splash screen includes a touchscreen-driven Bluetooth setup UI (tap "Setup" on the idle screen). This uses `piauto.bt_pair` which performs BR/EDR discovery and pairing via dbus-next with persistent D-Bus connections.

To pair manually from the command line:

```bash
# Scan for BR/EDR devices
python3 -m piauto.bt_pair scan

# Pair, trust, and connect
python3 -m piauto.bt_pair pair XX:XX:XX:XX:XX:XX
```

> **Note:** BR/EDR discovery must run as the `pi` user, not root. Running as root may miss some devices due to BlueZ D-Bus policy differences. The PiAuto service (which runs as root) automatically uses `sudo -u pi` when launching bt_pair.

> **Note:** `bluetoothctl` is not reliable for BR/EDR discovery in non-interactive mode because it drops the D-Bus connection too quickly for inquiry results to appear. The `bt_pair` module keeps the connection alive for the full discovery duration.

WirePlumber will auto-reconnect to paired speakers on subsequent boots.

Optionally set the speaker MAC in `/data/piauto.yaml`:

```yaml
bluetooth:
  speaker_mac: "XX:XX:XX:XX:XX:XX"
```

---

## 11. WiFi AP+STA (Dual Interface)

The Pi 4B's BCM43455 WiFi chip supports running AP and station mode simultaneously on the same radio. This allows the Pi to maintain a connection to a home/infrastructure WiFi network (for SSH and internet) while also hosting an AP for Android Auto phone connections. Both connections are managed by NetworkManager — hostapd and dnsmasq are not used in this configuration.

### 11.1 Hardware Verification

Verify the chipset supports concurrent AP+STA:

```bash
iw phy phy0 info | grep -A 6 "valid interface combinations"
```

Expected output includes `#{ managed } <= 1, #{ AP } <= 1` with `#channels <= 1`. The channel constraint means both interfaces must operate on the same channel — the AP channel automatically follows the station's channel.

### 11.2 Create the Virtual AP Interface

Create a udev rule to persist the virtual interface across reboots:

```bash
# Get wlan0's MAC address
MAC=$(cat /sys/class/net/wlan0/address)
# Derive AP MAC by setting the locally-administered bit
AP_MAC=$(echo "$MAC" | sed 's/^\(..\)/\x02&/' | head -c 17)

sudo tee /etc/udev/rules.d/90-uap0.rules << EOF
ACTION=="add", SUBSYSTEM=="ieee80211", KERNEL=="phy*", RUN+="/usr/sbin/iw phy %k interface add uap0 type __ap", RUN+="/bin/ip link set dev uap0 address ${AP_MAC}"
EOF
```

After creating this file, reload udev rules or reboot:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 11.3 Configure NetworkManager

Create the AP connection profile for `uap0`. The deployed configuration uses SSID `PiAuto` and passphrase `piauto1234` on the 192.168.50.0/24 subnet:

```bash
nmcli connection add type wifi ifname uap0 con-name piauto-ap \
  autoconnect yes \
  wifi.mode ap \
  wifi.band a \
  wifi.ssid "PiAuto" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "piauto1234" \
  ipv4.method shared \
  ipv4.addresses 192.168.50.1/24 \
  ipv6.method disabled
```

> **Channel note:** The AP channel is determined at runtime by the station's channel (both must match because they share one radio). The `wifi.channel` setting in the NM profile is overridden by the driver.

> **Password note:** Change `piauto1234` to a stronger passphrase before deployment. Update both this NM profile and `/data/piauto.yaml` (`wifi.password`) — the RFCOMM credential exchange sends the value from the YAML config to the phone.

The `wlan0` STA connection is managed by the existing NM profile (e.g., `netplan-wlan0-Graydons5G` or whichever profile connected during initial setup).

### 11.4 Create the Boot Service

NetworkManager brings up connections on demand, but the AP+STA combination requires a retry loop to ensure both interfaces are active before PiAuto starts. Create `/etc/systemd/system/piauto-wifi.service`:

```ini
[Unit]
Description=PiAuto WiFi AP+STA bringup
Before=piauto.service
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
  for i in $(seq 1 30); do \
    nmcli -t -f DEVICE,STATE dev | grep -q "uap0:connected" && \
    nmcli -t -f DEVICE,STATE dev | grep -q "wlan0:connected" && break; \
    sleep 1; \
  done'

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable piauto-wifi.service
```

### 11.5 Disable WiFi Power Saving

Prevent latency spikes and connection drops:

```bash
sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf << 'EOF'
[connection]
wifi.powersave = 2
EOF
```

### 11.6 Verification

After reboot, both interfaces should be active:

```bash
nmcli device status
# wlan0   wifi  connected  netplan-wlan0-Graydons5G  (or your STA profile)
# uap0    wifi  connected  piauto-ap
```

Check that the Pi has an IP on uap0:

```bash
ip addr show uap0 | grep "inet "
# inet 192.168.50.1/24
```

### 11.7 PiAuto WifiManager Integration

When `uap0` is present and the NM `piauto-ap` profile is connected, `piauto/wifi.py`'s `_check_nm_managed_ap()` returns `True` and the `WifiManager` skips starting hostapd and dnsmasq. The AP is already up before PiAuto starts (via `piauto-wifi.service`). PiAuto only needs to verify connectivity before sending RFCOMM credentials to the phone.

### 11.8 Known Limitations

- **Same channel constraint:** Both interfaces share one radio, so they must be on the same channel. The AP follows the STA channel automatically.
- **5 GHz required for both:** If your home WiFi is 5 GHz, the AP will also be 5 GHz (and vice versa for 2.4 GHz). Android phones support both bands.
- **Firmware stability:** Under heavy concurrent traffic, the brcmfmac driver may occasionally crash. This is rare under normal PiAuto usage.
- **Channel switching:** If the STA roams to a different channel, AP clients briefly disconnect during the channel switch.

---

## 12. Qt EGLFS Configuration

### 12.1 EGLFS KMS Config

Create `/data/eglfs.json`:

```json
{
    "device": "/dev/dri/card0",
    "outputs": [
        {
            "name": "HDMI2",
            "mode": "1024x600"
        }
    ]
}
```

> **Important notes:**
> - On Pi 4, **card0** (`platform-gpu`) is the KMS display controller. **card1** (`v3d`) is the render-only GPU — it will fail with `drmModeGetResources failed` if used for display. Do not be misled by connector entries under `/sys/class/drm/card1-*` — those are sysfs aliases.
> - The Pi 4 has two micro-HDMI ports: HDMI-A-1 (`HDMI1`, micro-HDMI 0) and HDMI-A-2 (`HDMI2`, micro-HDMI 1). Check which is connected:
>   ```bash
>   for f in /sys/class/drm/card*-HDMI-*/status; do echo "$f: $(cat $f)"; done
>   ```
> - The LCDWiki 7" HDMI-B display reports **1024x600** native resolution (not 800x480 as marketed).

### 12.2 Verify EGLFS Works

```bash
QT_QPA_PLATFORM=eglfs /usr/local/bin/autoapp
# Should render to the display directly (no X11/Wayland)
```

---

## 13. Verification Checklist

Run these checks after setup to confirm everything is ready:

| # | Check | Command | Expected |
| :- | :---- | :------ | :------- |
| 1 | OS version | `cat /etc/os-release` | Trixie, 64-bit |
| 2 | Python version | `python3 --version` | 3.11+ |
| 3 | BlueZ running | `systemctl status bluetooth` | active |
| 4 | PipeWire running | `systemctl --user status pipewire` | active |
| 5 | GPU memory | `vcgencmd get_mem gpu` | 128M |
| 6 | Display resolution | `cat /sys/class/drm/card0-HDMI-A-2/modes \| head -1` | 1024x600 |
| 7 | /data mounted | `mount \| grep /data` | ext4, rw |
| 8 | OpenAuto binary | `ls -la /usr/local/bin/autoapp` | exists |
| 9 | OpenAuto library | `ls -la /opt/openauto/lib/libopenauto.so.2` | exists |
| 10 | hostapd available | `which hostapd` | /usr/sbin/hostapd |
| 11 | dnsmasq available | `which dnsmasq` | /usr/sbin/dnsmasq |
| 12 | GPIO accessible | `gpioinfo 4` | shows lines |
| 13 | WiFi 5 GHz | `iw phy phy0 info \| grep 5180` | 5 GHz bands listed |
| 14 | User linger | `loginctl show-user pi -p Linger` | Linger=yes |
| 15 | WiFi AP+STA | `nmcli device status \| grep uap0` | connected (piauto-ap) |
| 16 | WiFi AP IP | `ip addr show uap0 \| grep "inet "` | 192.168.50.1/24 |
| 17 | piauto-wifi service | `systemctl status piauto-wifi` | active (exited) |
| 18 | WirePlumber bluez | `ls ~/.config/wireplumber/wireplumber.conf.d/50-bluez-config.conf` | exists |
| 19 | WiFi power save | `cat /etc/NetworkManager/conf.d/wifi-powersave.conf` | wifi.powersave = 2 |
| 20 | udev uap0 rule | `cat /etc/udev/rules.d/90-uap0.rules` | exists, references uap0 |
| 21 | piauto service | `systemctl status piauto` | enabled |
| 22 | Config file | `cat /data/piauto.yaml` | valid YAML |
| 23 | GStreamer H.264 decoder | `gst-inspect-1.0 v4l2h264dec \|\| gst-inspect-1.0 avdec_h264` | at least one found |
| 24 | GStreamer appsrc/appsink | `gst-inspect-1.0 appsrc && gst-inspect-1.0 appsink` | both found |
| 25 | aasdk fork | `git -C /opt/aasdk rev-parse HEAD 2>/dev/null \|\| git -C /tmp/aasdk rev-parse HEAD` | commit 7f84303 |
| 26 | openauto fork | `git -C /opt/openauto rev-parse HEAD` | commit ee75ebc or later |
| 27 | libinput disabled | `grep QT_QPA_EGLFS_NO_LIBINPUT /etc/systemd/system/piauto.service 2>/dev/null \|\| echo "set in subprocess env"` | variable is set |

---

## 14. First Boot Sequence

After completing all setup steps:

1. Enable the read-only root filesystem (§6.3).
2. Reboot.
3. The PiAuto service starts automatically.
4. Expected boot sequence:
   - Splash screen appears ("Starting...")
   - Transitions to "Waiting for phone..."
   - Pi is advertising BLE WAA service
5. On your Android phone:
   - Go to Settings → Connected devices → Connection preferences → Android Auto
   - Enable "Add new cars to Android Auto"
   - The phone should discover "PiAuto"
   - Accept pairing
   - Phone joins the Pi's WiFi AP automatically
   - Android Auto projection starts on the display

---

## 15. Troubleshooting

### Logs

```bash
# View PiAuto service logs
journalctl -u piauto -f

# View BlueZ logs
journalctl -u bluetooth -f

# View all PiAuto namespace logs
journalctl -t piauto -f
```

### Common Issues

| Symptom | Likely Cause | Fix |
| :------ | :----------- | :-- |
| No splash screen | EGLFS can't acquire DRM | Check `/data/eglfs.json` device path. Ensure no other process holds DRM. |
| BLE not advertising | BT soft-blocked or BlueZ not running | `rfkill unblock bluetooth && systemctl restart bluetooth`. Check `rfkill list`. |
| Phone won't connect to PiAuto AP (AP+STA mode) | `uap0` not up or NM profile not connected | Run `nmcli device status`. If `uap0` is absent, check udev rule (`/etc/udev/rules.d/90-uap0.rules`). If `uap0` exists but is disconnected, run `nmcli connection up piauto-ap`. Check `systemctl status piauto-wifi`. |
| Phone won't connect to WiFi (standalone mode) | hostapd failed (bad channel/country) | Check `journalctl -u piauto` for hostapd errors. Verify `wifi.country` matches your region. |
| No audio from speaker | A2DP not connected | `bluetoothctl connect XX:XX:XX:XX:XX:XX`. Check `wpctl status`. |
| `profile-unavailable` on BT connect | WirePlumber A2DP endpoints not registered | Check WirePlumber is running: `systemctl --user status wireplumber`. Ensure seat monitoring is disabled (§10.2) and linger is enabled (§8.3). |
| BT speaker disconnects after SSH logout | User session killed, PipeWire/WirePlumber stopped | Enable linger: `sudo loginctl enable-linger pi` (§8.3). |
| `bluetoothctl scan` finds nothing (BR/EDR) | D-Bus connection drops too quickly | Use `python3 -m piauto.bt_pair scan` instead — it keeps the D-Bus connection alive (§10.3). |
| BT scan as root misses devices | BlueZ D-Bus policy differs for root | Run bt_pair as pi user: `sudo -u pi python3 -m piauto.bt_pair scan`. The service does this automatically. |
| `br-connection-busy` on BT connect | Stale ACL connection from previous attempt | Disconnect first: `bluetoothctl disconnect XX:XX:XX:XX:XX:XX`, wait 3s, then retry. |
| Boot timeout (60 s) | Service dependency not met | Check which service failed: `systemctl --failed`. |
| OpenAuto crash / `libopenauto.so.2` not found | Library not at RPATH | Verify `/opt/openauto/lib/libopenauto.so.2` exists. The binary RPATH points there; do not move or rename the lib directory. |
| OpenAuto crash / DRM issue | DRM access or missing plugins | Run `/usr/local/bin/autoapp` manually and check stderr. |
| Video renders but touch has 3–8s lag | Post-decoder queue backlog | Rebuild with the current piauto-debian13 branch — the leaky post-decoder queue fix is required. |
| Double tap on touchscreen | libinput double-registration | Ensure `QT_QPA_EGLFS_NO_LIBINPUT=1` and `QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS` are set in the OpenAuto subprocess environment (§17 of Implementation Guide). |
| Audio stutter on notifications/Gemini | RtAudio race condition | Fixed in `AndrewGraydon/openauto` piauto-debian13 branch via OpenDsh PR #32 static mutex. Rebuild per §4.2 with `-DGST_BUILD=ON`. |
| No video or wrong video size on EGLFS | Built without `-DGST_BUILD=ON` | Rebuild per §4.2 with `-DGST_BUILD=ON`. Rollback: `sudo cp /usr/local/bin/autoapp-2026.03.28+git.4cc739b /usr/local/bin/autoapp`. |
| GStreamer pipeline fails to start | Missing gstreamer plugins | Run: `apt install gstreamer1.0-plugins-bad gstreamer1.0-libav`. Check: `gst-inspect-1.0 h264parse`. |
