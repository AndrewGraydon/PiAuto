# Raspberry Pi Setup Guide: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-PSG-001               |
| Version        | 1.0                          |
| Date           | 2026-03-27                   |
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
    libgps-dev
```

---

## 4. Build OpenAuto and aasdk

### 4.1 Build aasdk

The upstream aasdk (opencardev/aasdk) already includes `TCPTransport` for wireless AA.

```bash
cd /opt
sudo git clone https://github.com/opencardev/aasdk.git
cd aasdk

sudo mkdir build && cd build
sudo cmake -DCMAKE_BUILD_TYPE=Release \
    -DSKIP_BUILD_PROTOBUF=ON \
    -DSKIP_BUILD_ABSL=ON \
    ..
sudo make -j$(nproc)
sudo make install
sudo ldconfig
```

> **Note:** `-DSKIP_BUILD_PROTOBUF=ON -DSKIP_BUILD_ABSL=ON` uses the system protobuf (3.21) instead of downloading protobuf v30 + abseil, which avoids build conflicts on Trixie.

### 4.2 Build OpenAuto

```bash
cd /opt
sudo git clone https://github.com/opencardev/openauto.git
cd openauto

sudo mkdir build && cd build
sudo cmake -DCMAKE_BUILD_TYPE=Release -DNOPI=ON ..
sudo make -j$(nproc)
sudo make install
```

> **Note:** `-DNOPI=ON` disables the Broadcom OMX/VideoCore (`bcm_host.h`) video path which doesn't exist on Trixie. Video output uses Qt's rendering pipeline instead. The resulting binary is `/usr/local/bin/autoapp`.

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

> Video Resolution=1 is 800x480 (the AA protocol maximum at 30 FPS). The display scales this to its native 1024x600.

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

The Pi 4B's BCM43455 WiFi chip supports running AP and station mode simultaneously on the same radio. This allows the Pi to maintain a connection to a home/infrastructure WiFi network (for SSH and internet) while also hosting an AP for Android Auto phone connections.

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
ACTION=="add", SUBSYSTEM=="ieee80211", KERNEL=="phy0", RUN+="/usr/sbin/iw phy phy0 interface add uap0 type __ap", RUN+="/bin/ip link set dev uap0 address ${AP_MAC}"
EOF
```

### 11.3 Configure NetworkManager

Create the AP connection profile:

```bash
nmcli connection add type wifi ifname uap0 con-name piauto-ap \
  autoconnect yes \
  wifi.mode ap \
  wifi.band a \
  wifi.channel 48 \
  wifi.ssid "PiAuto" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "your-ap-password" \
  ipv4.method shared \
  ipv4.addresses 192.168.50.1/24 \
  ipv6.method disabled
```

> **Channel note:** The AP channel is overridden by the station's channel at runtime (both must match). Set it to your home router's channel for initial config, but the driver will auto-sync.

### 11.4 Disable WiFi Power Saving

Prevent latency spikes and connection drops:

```bash
sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf << 'EOF'
[connection]
wifi.powersave = 2
EOF
```

### 11.5 Verification

After reboot, both interfaces should be active:

```bash
nmcli device status
# wlan0   wifi  connected  your-home-wifi
# uap0    wifi  connected  piauto-ap
```

### 11.6 Known Limitations

- **Same channel constraint:** Both interfaces share one radio, so they must be on the same channel. The AP follows the STA channel automatically.
- **5 GHz required for both:** If your home WiFi is 5 GHz, the AP will also be 5 GHz (and vice versa for 2.4 GHz). Android phones support both.
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
| 9 | hostapd available | `which hostapd` | /usr/sbin/hostapd |
| 10 | dnsmasq available | `which dnsmasq` | /usr/sbin/dnsmasq |
| 11 | GPIO accessible | `gpioinfo 4` | shows lines |
| 12 | WiFi 5 GHz | `iw phy phy0 info \| grep 5180` | 5 GHz bands listed |
| 13 | User linger | `loginctl show-user pi -p Linger` | Linger=yes |
| 14 | WiFi AP+STA | `nmcli device status \| grep uap0` | connected (piauto-ap) |
| 15 | WirePlumber bluez | `ls ~/.config/wireplumber/wireplumber.conf.d/50-bluez-config.conf` | exists |
| 16 | WiFi power save | `cat /etc/NetworkManager/conf.d/wifi-powersave.conf` | wifi.powersave = 2 |
| 17 | piauto service | `systemctl status piauto` | enabled |
| 18 | Config file | `cat /data/piauto.yaml` | valid YAML |

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
| Phone won't connect to WiFi | hostapd failed (bad channel/country) | Check `journalctl -u piauto` for hostapd errors. Verify `wifi.country` matches your region. |
| No audio from speaker | A2DP not connected | `bluetoothctl connect XX:XX:XX:XX:XX:XX`. Check `wpctl status`. |
| `profile-unavailable` on BT connect | WirePlumber A2DP endpoints not registered | Check WirePlumber is running: `systemctl --user status wireplumber`. Ensure seat monitoring is disabled (§10.2) and linger is enabled (§8.3). |
| BT speaker disconnects after SSH logout | User session killed, PipeWire/WirePlumber stopped | Enable linger: `sudo loginctl enable-linger pi` (§8.3). |
| `bluetoothctl scan` finds nothing (BR/EDR) | D-Bus connection drops too quickly | Use `python3 -m piauto.bt_pair scan` instead — it keeps the D-Bus connection alive (§10.3). |
| BT scan as root misses devices | BlueZ D-Bus policy differs for root | Run bt_pair as pi user: `sudo -u pi python3 -m piauto.bt_pair scan`. The service does this automatically. |
| `br-connection-busy` on BT connect | Stale ACL connection from previous attempt | Disconnect first: `bluetoothctl disconnect XX:XX:XX:XX:XX:XX`, wait 3s, then retry. |
| Boot timeout (60 s) | Service dependency not met | Check which service failed: `systemctl --failed`. |
| OpenAuto crash | Missing libraries or DRM issue | Run `/usr/local/bin/autoapp` manually and check stderr. |
