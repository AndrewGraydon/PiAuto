# Hardware & Power Specification: PiAuto

| Field          | Value                        |
| :------------- | :--------------------------- |
| Document ID    | PiAuto-HW-001                |
| Version        | 3.1                          |
| Date           | 2026-03-29                   |
| Status         | Draft                        |

## 1. Introduction

### 1.1 Purpose

This document specifies the hardware platform, power supply, display, peripheral connections, GPIO allocation, and thermal management for the PiAuto prototype head unit.

### 1.2 References

| ID              | Document                         |
| :-------------- | :------------------------------- |
| PiAuto-SRS-001  | Software Requirements Specification |
| PiAuto-ARCH-001 | Architecture Document            |

---

## 2. Target Platform

| Parameter        | Value                                    |
| :--------------- | :--------------------------------------- |
| Board            | Raspberry Pi 4 Model B                   |
| RAM              | 4 GB LPDDR4                              |
| SoC              | Broadcom BCM2711 (Cortex-A72, 4-core, 1.8 GHz) |
| GPU              | VideoCore VI (H.264 HW decode, OpenGL ES 3.1) |
| Wi-Fi            | 802.11ac dual-band (on-board)            |
| Bluetooth        | BT 5.0 (BLE + Classic, on-board)         |
| USB              | 2× USB 3.0, 2× USB 2.0                  |
| HDMI             | 2× micro-HDMI (HDMI0 used)              |
| Storage          | MicroSD slot                             |

---

## 3. Power Architecture

### 3.1 Overview

The Pi is powered from the vehicle's 12 V DC bus via an external buck converter. Automotive electrical systems are noisy (load dumps, alternator ripple), so the converter must provide clean, stable 5.1 V output.

### 3.2 Power Specifications

| Parameter              | Value                              |
| :--------------------- | :--------------------------------- |
| Vehicle Input          | 12 V DC nominal (9–16 V operating range) |
| Buck Converter Output  | 5.1 V DC                          |
| Continuous Current     | 3.5 A minimum                     |
| Peak Power (Pi + Display) | ~21 W (5.1 V × 3.5 A + display 3.7 W) |
| Connector to Pi        | USB-C (official Pi 4 power input)  |

**Note:** Power budget is not a design constraint for this prototype (vehicle supply is adequate). The specifications above reflect actual measured draw under full load.

### 3.3 Power Management Logic

The system uses an ignition-sense circuit to manage clean boot and shutdown:

| Step | Condition       | Action                                           |
| :--- | :-------------- | :----------------------------------------------- |
| 1    | Ignition ON     | Buck converter activates → Pi boots              |
| 2    | Ignition OFF    | GPIO 17 detects LOW → state machine triggers `shutdown -h now` |
| 3    | Delay OFF       | Hardware timer maintains power for 30 seconds after ignition OFF to allow clean shutdown |

### 3.4 Delay-OFF Circuit

The buck converter's enable pin is held HIGH by a simple RC delay circuit (or a dedicated power latch module such as a Pololu mini pushbutton power switch). This ensures power is maintained for the full shutdown duration regardless of ignition state.

| Parameter      | Value     |
| :------------- | :-------- |
| Hold Time      | 30 seconds minimum |
| Trigger        | Ignition line going LOW |
| Release        | After hold time expires, or Pi asserts GPIO shutdown-complete signal |

---

## 4. Display

### 4.1 Specifications

| Parameter          | Value                                    |
| :----------------- | :--------------------------------------- |
| Model              | LCDWiki 7-inch HDMI Display-B           |
| Screen Size        | 7.0 inches (diagonal)                   |
| Native Resolution  | 1024 × 600 (confirmed via KMS EDID; marketed as 800×480 but reports 1024×600) |
| Touch Type         | 5-point capacitive (wch.cn USB2IIC_CTP_CONTROL) |
| Touch Interface    | USB (Micro-USB cable to Pi USB 2.0 port)|
| Video Interface    | HDMI (Micro-HDMI to HDMI adapter/cable) |
| Backlight          | 160 cd/m²                               |
| Power Consumption  | 3.7 W (5 V × 0.74 A, via USB)          |
| Active Area        | 154.21 × 85.92 mm                       |
| Outer Dimensions   | 164.9 × 124.27 × 15.15 mm              |
| Certifications     | CE, RoHS                                |

### 4.2 Raspberry Pi Configuration (config.txt)

With the KMS display driver (`dtoverlay=vc4-kms-v3d`, which is the default on RPi OS Trixie), **do not add legacy `hdmi_group`/`hdmi_mode`/`hdmi_cvt` settings** — they conflict with KMS and cause a blank screen. The KMS driver reads display capabilities via EDID and auto-configures to the native resolution (1024×600).

Add only:

```ini
# GPU memory for V4L2 H.264 decode + Qt EGLFS
gpu_mem=128

# SD card overclock for faster boot (optional)
dtparam=sd_overclock=100
```

Qt EGLFS is configured via `/data/eglfs.json` (see PiSetup §12.1).

### 4.3 Connections

| Pi Port           | Cable                       | Display Port      |
| :---------------- | :-------------------------- | :---------------- |
| Micro-HDMI (HDMI0)| Micro-HDMI to HDMI cable   | HDMI input        |
| USB 2.0 (any)     | USB-A to Micro-USB cable   | USB touch + power |

**Note:** The display's USB connection provides both touch input and power to the display. No separate power cable is required.

---

## 5. GPIO Allocation

### 5.1 Pin Map

| Physical Pin | BCM GPIO | Function        | Direction | Electrical Notes                    | Satisfies |
| :----------- | :------- | :-------------- | :-------- | :---------------------------------- | :-------- |
| 7            | 4        | Fan PWM         | Output    | 25 kHz PWM, 3.3 V logic → MOSFET gate to 5 V/12 V fan | FR-035 |
| 11           | 17       | Ignition Sense  | Input     | Internal pull-up. LOW = ignition OFF. 500 ms debounce. | FR-032, FR-033 |

### 5.2 Removed from Previous Revision

| Previous Pin | Previous Function | Reason Removed                               |
| :----------- | :---------------- | :------------------------------------------- |
| 13 (GPIO 27) | Reverse Signal   | Reverse camera is out of scope for prototype |
| 3 (GPIO 2, SDA) | Touchscreen I2C | Display uses USB for touch, not I2C       |
| 5 (GPIO 3, SCL) | Touchscreen I2C | Display uses USB for touch, not I2C       |

### 5.3 Fan Circuit

The Pi's GPIO 4 cannot directly drive a 12 V fan. A switching circuit is required:

```
GPIO 4 ──► [10kΩ resistor] ──► MOSFET Gate (e.g., IRLZ44N)
                                  │
                                  Drain ──► Fan (−)
                                  │
                                  Source ──► GND

Fan (+) ──► 12 V supply (or 5 V for small fans)
```

A flyback diode (1N4007) across the fan terminals protects the MOSFET from back-EMF.

---

## 6. Audio Hardware

Two options are supported. The choice is made at build time and configured in `/data/piauto.yaml`.

### Option A: USB Audio Interface (Recommended)

| Parameter      | Value                              |
| :------------- | :--------------------------------- |
| Type           | USB Audio Class 1.0 or 2.0 dongle |
| Connection     | Pi USB 2.0 port                   |
| Driver         | snd-usb-audio (kernel built-in)   |
| SNR            | Typically > 90 dB                  |
| Use Case       | Higher quality audio, analog output if needed alongside BT |

### Option B: I2S DAC (PCM5102A)

| Parameter      | Value                              |
| :------------- | :--------------------------------- |
| Type           | PCM5102A breakout board            |
| Connection     | GPIO header (I2S pins: GPIO 18, 19, 21) |
| Driver         | `dtoverlay=hifiberry-dac` in config.txt |
| SNR            | ~112 dB                           |
| Use Case       | Compact build, no USB port consumed |

### Bluetooth Adapter Configuration

Two Bluetooth adapters are used to eliminate WiFi/BT radio contention:

| Adapter | Interface | MAC | Role |
| :------ | :-------- | :-- | :--- |
| BCM43455 (on-board) | hci0 | e4:5f:01:0c:82:9e | BLE WAA discovery + RFCOMM credential exchange |
| USB BT dongle (CSR8510 or equivalent) | hci1 | 00:19:86:00:14:BB | BT A2DP audio output to vehicle speaker |

The BCM43455 shares a physical radio with WiFi. Under concurrent WiFi AP + BT A2DP load, contention causes audio dropouts. Offloading A2DP to a dedicated USB BT adapter (hci1) eliminates this.

### Primary Audio Path

For this prototype, audio output is via **Bluetooth A2DP** to a vehicle speaker using the USB BT dongle (hci1). The on-board BT (hci0) handles BLE and RFCOMM. PipeWire/WirePlumber routes audio to the hci1 A2DP sink.

---

## 7. Thermal Management

### 7.1 Thermal Constraints

| Parameter            | Value                              |
| :------------------- | :--------------------------------- |
| Pi 4 Throttle Temp   | 80 °C                             |
| Ambient (in-vehicle) | Up to 50 °C (dashboard, summer)   |
| Target Max CPU Temp  | 70 °C (10 °C margin to throttle)  |

### 7.2 Passive Cooling

Heatsinks are mandatory on:

- BCM2711 SoC (CPU/GPU)
- LPDDR4 RAM package
- VL805 USB 3.0 controller

Thermal adhesive or thermal tape shall be used for attachment.

### 7.3 Active Cooling (Fan Profile)

| CPU Temperature  | PWM Duty Cycle | Fan Behavior |
| :--------------- | :------------- | :----------- |
| < 50 °C          | 0 %            | OFF          |
| 50 °C – 65 °C   | 50 %           | Medium       |
| > 65 °C          | 100 %          | Full speed   |

- Polling interval: 5 seconds
- Temperature source: `/sys/class/thermal/thermal_zone0/temp`
- Hysteresis: 3 °C (fan does not drop speed until temp falls 3 °C below the threshold)

---

## 8. Storage

| Parameter        | Value                                    |
| :--------------- | :--------------------------------------- |
| Type             | MicroSD (SDHC or SDXC)                  |
| Capacity         | 16 GB minimum                           |
| Endurance Rating | High-endurance recommended (e.g., Samsung PRO Endurance, SanDisk Max Endurance) |
| Filesystem       | See partition layout in PiAuto-ARCH-001 §7.2 |
| Read-Only Root   | Yes — overlayfs with tmpfs overlay       |

High-endurance cards are specified because, despite the read-only root filesystem, the writable `/data` partition will see periodic writes (pairing records, config changes), and automotive environments involve power cycling.

---

## 9. Enclosure

This is a prototype build. The enclosure is user-provided (existing off-the-shelf Raspberry Pi case). No formal enclosure specification is defined.

**Prototype considerations:**

- Ensure adequate ventilation for the fan and heatsinks.
- Route cables (HDMI, USB touch, USB power, GPIO wires) without strain on connectors.
- Secure the Pi to prevent vibration-induced disconnections during vehicle operation.

---

## 10. Bill of Materials (Prototype)

| Qty | Item                                     | Purpose                  |
| :-- | :--------------------------------------- | :----------------------- |
| 1   | Raspberry Pi 4 Model B (4 GB)           | Compute platform         |
| 1   | LCDWiki 7" HDMI Display-B               | Display + touch input    |
| 1   | 16 GB High-Endurance MicroSD            | OS + storage             |
| 1   | 12 V → 5.1 V / 3.5 A buck converter    | Power supply             |
| 1   | Micro-HDMI to HDMI cable                | Video to display         |
| 1   | USB-A to Micro-USB cable                | Touch + display power    |
| 1   | USB-C power cable (from buck converter)  | Pi power input           |
| 1   | Heatsink kit (CPU + RAM + USB controller)| Passive cooling          |
| 1   | 5 V or 12 V PWM fan (25–40 mm)          | Active cooling           |
| 1   | IRLZ44N N-channel MOSFET                | Fan switching            |
| 1   | 1N4007 diode                             | Flyback protection       |
| 1   | 10 kΩ resistor                           | MOSFET gate pull-down    |
| 1   | USB Bluetooth dongle (CSR8510 or equivalent, A2DP-capable) | Dedicated BT A2DP adapter (hci1) for speaker audio, eliminating WiFi/BT radio contention on hci0 |
| 1   | USB audio dongle OR PCM5102A DAC board   | Audio output (optional)  |
| —   | Hookup wire, connectors, mounting hardware| Assembly                |
