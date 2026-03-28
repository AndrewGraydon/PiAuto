"""Splash screen application and subprocess manager.

Satisfies: FR-036 (splash in IDLE), FR-037 (status during connection).
See PiAuto-IG-001 §9.

This module serves two purposes:
1. When run as `python -m piauto.splash "Status text"`, it displays a
   full-screen PyQt5 EGLFS window with the given status message.
2. When imported, it provides `launch_splash()` / `kill_splash()` for
   the state machine to manage the splash as a subprocess.

The splash runs as a separate process to maintain the DRM master
ownership invariant (only one process owns DRM at a time).
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
from pathlib import Path

from piauto.log import get_logger

log = get_logger("splash")

# DRM handoff safety margin (ms) — see PiAuto-IG-001 §9.3
DRM_RELEASE_DELAY = 0.5

EGLFS_CONFIG_PATH = Path("/data/eglfs.json")


def detect_drm_card() -> str:
    """Detect the DRM card that supports KMS modesetting.

    On Pi 4, card0 and card1 can swap between reboots (platform-gpu vs v3d).
    The display controller is the one where drmModeGetResources succeeds.
    Returns the device path, e.g. '/dev/dri/card1'.
    """
    try:
        libdrm = ctypes.CDLL("libdrm.so.2")
    except OSError:
        log.warning("libdrm not available — defaulting to /dev/dri/card0")
        return "/dev/dri/card0"

    for i in range(2):
        path = f"/dev/dri/card{i}"
        try:
            fd = os.open(path, os.O_RDWR)
            try:
                res = libdrm.drmModeGetResources(fd)
                if res:
                    log.info("DRM display card detected: %s", path)
                    return path
            finally:
                os.close(fd)
        except OSError:
            continue

    log.warning("No KMS-capable DRM card found — defaulting to /dev/dri/card0")
    return "/dev/dri/card0"


def write_eglfs_config(
    output_name: str = "HDMI2",
    mode: str = "1024x600",
) -> Path:
    """Detect the correct DRM card and write /data/eglfs.json.

    Called once at boot to handle Pi 4 card0/card1 swapping.
    """
    device = detect_drm_card()
    config = {
        "device": device,
        "outputs": [{"name": output_name, "mode": mode}],
    }
    EGLFS_CONFIG_PATH.write_text(json.dumps(config, indent=4))
    log.info("EGLFS config written: %s → %s %s", device, output_name, mode)
    return EGLFS_CONFIG_PATH


def _detect_touchscreen_device() -> str:
    """Find the evdev node for the USB touchscreen.

    Scans /proc/bus/input/devices for a device with INPUT_PROP_DIRECT
    (property bit 1), which indicates a touchscreen. Returns the
    /dev/input/eventN path, or empty string if not found.
    """
    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
    except OSError:
        return ""

    current_handlers = ""
    for line in content.splitlines():
        if line.startswith("H: Handlers="):
            current_handlers = line
        elif line.startswith("B: PROP=") and current_handlers:
            # PROP bit 1 = INPUT_PROP_DIRECT (touchscreen)
            try:
                prop_val = int(line.split("=")[1], 16)
            except (ValueError, IndexError):
                continue
            if prop_val & 0x2:
                # Extract eventN from handlers line
                for part in current_handlers.split():
                    if part.startswith("event"):
                        device = f"/dev/input/{part}"
                        log.info("Touchscreen detected: %s", device)
                        return device
        elif line == "":
            current_handlers = ""

    log.warning("No touchscreen device found")
    return ""


class SplashManager:
    """Manages the splash screen subprocess."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    def _build_env(self) -> dict[str, str]:
        """Build environment for splash/UI subprocesses."""
        return {
            **os.environ,
            "QT_QPA_PLATFORM": "eglfs",
            "QT_QPA_EGLFS_KMS_CONFIG": os.environ.get(
                "QT_QPA_EGLFS_KMS_CONFIG", "/data/eglfs.json"
            ),
            "QT_QPA_EGLFS_HIDECURSOR": "1",
            "QT_QPA_GENERIC_PLUGINS": "evdevtouch",
            "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS": os.environ.get(
                "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS",
                _detect_touchscreen_device(),
            ),
        }

    async def launch(self, status_text: str) -> None:
        """Launch the splash screen with the given status text.

        If a splash is already running, kills it first.
        """
        await self.kill()

        cmd = [sys.executable, "-m", "piauto.splash", status_text]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            log.info("Splash launched (pid %d): %s", self._proc.pid, status_text)
        except (FileNotFoundError, PermissionError) as exc:
            log.warning("Failed to launch splash: %s", exc)

    async def launch_bt_setup(self) -> None:
        """Launch the Bluetooth speaker pairing UI."""
        await self.kill()

        cmd = [sys.executable, "-m", "piauto.splash", "--bt-setup"]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            log.info("BT setup UI launched (pid %d)", self._proc.pid)
        except (FileNotFoundError, PermissionError) as exc:
            log.warning("Failed to launch BT setup UI: %s", exc)

    async def read_stdout_line(self) -> str | None:
        """Read a line from the splash subprocess stdout (non-blocking).

        Returns the line content, or None if not available / process exited.
        Used to receive signals like 'SETUP' or 'PAIRED:mac:name' from the UI.
        """
        if not self._proc or not self._proc.stdout:
            return None
        try:
            line = await self._proc.stdout.readline()
            if line:
                return line.decode().strip()
        except (asyncio.CancelledError, OSError):
            pass
        return None

    async def kill(self) -> None:
        """Send SIGTERM to the splash and wait for it to exit."""
        if not self._proc or self._proc.returncode is not None:
            self._proc = None
            return

        log.debug("Stopping splash (pid %d)", self._proc.pid)
        self._proc.terminate()

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=3)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()
            log.warning("Splash killed (did not stop in 3 s)")

        self._proc = None

    async def kill_and_wait_drm_release(self) -> None:
        """Kill the splash and wait for DRM master to be released.

        Must be called before launching OpenAuto to ensure it can
        acquire DRM master.
        """
        await self.kill()
        await asyncio.sleep(DRM_RELEASE_DELAY)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None


# === Splash screen Qt application (runs as __main__) ===

# Common stylesheet constants
_STYLE_BG = "background-color: #1a1a2e;"
_STYLE_BTN = (
    "QPushButton {"
    "  background-color: #16213e; color: white; border: 2px solid #0f3460;"
    "  border-radius: 8px; padding: 12px; font-size: 20px;"
    "}"
    "QPushButton:pressed { background-color: #0f3460; }"
)
_STYLE_BTN_ACCENT = (
    "QPushButton {"
    "  background-color: #0f3460; color: white; border: 2px solid #e94560;"
    "  border-radius: 8px; padding: 12px; font-size: 20px;"
    "}"
    "QPushButton:pressed { background-color: #e94560; }"
)
_STYLE_TITLE = "color: white; font-size: 28px; font-weight: bold;"
_STYLE_STATUS = "color: #aaa; font-size: 16px;"
_STYLE_SETUP_BTN = (
    "QPushButton {"
    "  background-color: #16213e; color: #aaa; border: 1px solid #0f3460;"
    "  border-radius: 6px; padding: 8px 16px; font-size: 14px;"
    "}"
    "QPushButton:pressed { background-color: #0f3460; color: white; }"
)


def _run_splash_app(status_text: str) -> None:
    """Run the splash screen Qt application (blocking)."""
    import signal

    try:
        from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget, QPushButton
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
    except ImportError:
        print(f"[splash] PyQt5 not available — printing status: {status_text}", file=sys.stderr)
        signal.pause()
        return

    app = QApplication(sys.argv)

    win = QWidget()
    win.setStyleSheet(_STYLE_BG)
    layout = QVBoxLayout(win)

    layout.addStretch(1)

    label = QLabel(status_text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(_STYLE_TITLE)
    layout.addWidget(label)

    layout.addStretch(1)

    # Setup button at bottom — writes "SETUP" to stdout for state machine
    setup_btn = QPushButton("Setup")
    setup_btn.setStyleSheet(_STYLE_SETUP_BTN)
    setup_btn.setFixedHeight(40)
    setup_btn.clicked.connect(lambda: print("SETUP", flush=True))
    layout.addWidget(setup_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    win.showFullScreen()

    def handle_sigterm(signum, frame):
        app.quit()

    signal.signal(signal.SIGTERM, handle_sigterm)
    sys.exit(app.exec_())


def _run_bt_setup_app() -> None:
    """Run the Bluetooth speaker pairing UI (blocking).

    Scans for BT audio devices via dbus-next (BR/EDR discovery) and allows
    pairing via touchscreen. Writes paired device MAC to stdout on success.
    """
    import signal
    import subprocess as sp

    try:
        from PyQt5.QtWidgets import (
            QApplication, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
            QPushButton, QScrollArea,
        )
        from PyQt5.QtCore import Qt, QTimer, QProcess
    except ImportError:
        print("[splash] PyQt5 not available", file=sys.stderr)
        signal.pause()
        return

    app = QApplication(sys.argv)

    # ── Main window ──
    win = QWidget()
    win.setStyleSheet(_STYLE_BG)
    main_layout = QVBoxLayout(win)
    main_layout.setContentsMargins(20, 15, 20, 15)

    # Title bar
    title_bar = QHBoxLayout()
    title = QLabel("Bluetooth Speaker Setup")
    title.setStyleSheet(_STYLE_TITLE)
    title_bar.addWidget(title)
    title_bar.addStretch()

    back_btn = QPushButton("Back")
    back_btn.setStyleSheet(_STYLE_BTN)
    back_btn.setFixedSize(100, 44)
    back_btn.clicked.connect(lambda: (print("BACK", flush=True), app.quit()))
    title_bar.addWidget(back_btn)
    main_layout.addLayout(title_bar)

    # Status label
    status = QLabel("Press Scan to find Bluetooth speakers")
    status.setStyleSheet(_STYLE_STATUS)
    status.setAlignment(Qt.AlignmentFlag.AlignCenter)
    main_layout.addWidget(status)

    # Scrollable device list
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    device_container = QWidget()
    device_container.setStyleSheet("background: transparent;")
    device_layout = QVBoxLayout(device_container)
    device_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(device_container)
    main_layout.addWidget(scroll, stretch=1)

    # Bottom buttons
    btn_bar = QHBoxLayout()
    scan_btn = QPushButton("Scan")
    scan_btn.setStyleSheet(_STYLE_BTN_ACCENT)
    scan_btn.setFixedHeight(50)
    btn_bar.addWidget(scan_btn)
    main_layout.addLayout(btn_bar)

    # ── BT scanning logic (uses piauto.bt_pair via QProcess) ──
    discovered: dict[str, str] = {}  # mac -> name
    scan_process: list[QProcess] = []  # mutable ref for closure
    pair_process: list[QProcess] = []

    def _clear_devices():
        while device_layout.count():
            item = device_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_device_button(mac: str, name: str):
        btn = QPushButton(f"{name}\n{mac}")
        btn.setStyleSheet(_STYLE_BTN)
        btn.setFixedHeight(60)
        btn.clicked.connect(lambda checked, m=mac, n=name: _pair_device(m, n, btn))
        device_layout.addWidget(btn)

    def _scan():
        status.setText("Scanning for speakers...")
        status.setStyleSheet("color: #e94560; font-size: 16px;")
        scan_btn.setEnabled(False)
        _clear_devices()
        discovered.clear()

        # Kill any previous scan
        for p in scan_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        scan_process.clear()

        # Launch bt_pair scan subprocess
        proc = QProcess(win)
        scan_process.append(proc)

        def _on_scan_stdout():
            while proc.canReadLine():
                line = bytes(proc.readLine()).decode().strip()
                if line.startswith("DEVICE|"):
                    # DEVICE|mac|name|class_hex
                    parts = line.split("|", 3)
                    if len(parts) >= 4:
                        mac, name = parts[1], parts[2]
                        if mac not in discovered:
                            discovered[mac] = name
                            _add_device_button(mac, name)
                elif line == "SCAN_DONE":
                    status.setText(
                        f"Found {len(discovered)} device(s) — tap to pair"
                    )
                    status.setStyleSheet(_STYLE_STATUS)
                    scan_btn.setEnabled(True)

        proc.readyReadStandardOutput.connect(_on_scan_stdout)
        # Run as pi user — BR/EDR discovery as root misses some devices
        proc.start("sudo", ["-u", "pi", sys.executable, "-m", "piauto.bt_pair", "scan"])

    def _pair_device(mac: str, name: str, btn: QPushButton):
        btn.setStyleSheet(
            "QPushButton { background-color: #e94560; color: white;"
            "  border: 2px solid #e94560; border-radius: 8px;"
            "  padding: 12px; font-size: 20px; }"
        )
        status.setText(f"Pairing with {name}...")
        status.setStyleSheet("color: #e94560; font-size: 16px;")

        # Kill any previous pair process
        for p in pair_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        pair_process.clear()

        proc = QProcess(win)
        pair_process.append(proc)

        def _on_pair_stdout():
            while proc.canReadLine():
                line = bytes(proc.readLine()).decode().strip()
                if line.startswith("PAIR_OK|"):
                    parts = line.split("|", 2)
                    paired_mac = parts[1] if len(parts) > 1 else mac
                    paired_name = parts[2] if len(parts) > 2 else name
                    status.setText(f"Connected to {paired_name}!")
                    status.setStyleSheet("color: #00ff88; font-size: 16px;")
                    btn.setStyleSheet(
                        "QPushButton { background-color: #00662a; color: white;"
                        "  border: 2px solid #00ff88; border-radius: 8px;"
                        "  padding: 12px; font-size: 20px; }"
                    )
                    print(f"PAIRED:{paired_mac}:{paired_name}", flush=True)
                elif line.startswith("PAIR_FAIL|"):
                    msg = line[len("PAIR_FAIL|"):]
                    status.setText(f"Failed: {msg}")
                    status.setStyleSheet("color: #ff4444; font-size: 16px;")
                    btn.setStyleSheet(_STYLE_BTN)

        proc.readyReadStandardOutput.connect(_on_pair_stdout)
        # Run as pi user — pairing as root fails (discovery can't find devices)
        proc.start("sudo", ["-u", "pi", sys.executable, "-m", "piauto.bt_pair", "pair", mac])

    scan_btn.clicked.connect(_scan)

    win.showFullScreen()

    def handle_sigterm(signum, frame):
        for p in scan_process + pair_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        app.quit()

    signal.signal(signal.SIGTERM, handle_sigterm)
    sys.exit(app.exec_())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--bt-setup":
        _run_bt_setup_app()
    else:
        text = sys.argv[1] if len(sys.argv) > 1 else "PiAuto"
        _run_splash_app(text)
