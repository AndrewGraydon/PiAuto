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

    async def launch(self, status_text: str) -> None:
        """Launch the splash screen with the given status text.

        If a splash is already running, kills it first.
        """
        await self.kill()

        cmd = [
            sys.executable, "-m", "piauto.splash",
            status_text,
        ]
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "eglfs",
            "QT_QPA_EGLFS_KMS_CONFIG": os.environ.get(
                "QT_QPA_EGLFS_KMS_CONFIG", "/data/eglfs.json"
            ),
            "QT_QPA_GENERIC_PLUGINS": "evdevtouch",
            "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS": os.environ.get(
                "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS",
                _detect_touchscreen_device(),
            ),
        }

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            log.info("Splash launched (pid %d): %s", self._proc.pid, status_text)
        except (FileNotFoundError, PermissionError) as exc:
            log.warning("Failed to launch splash: %s", exc)

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

def _run_splash_app(status_text: str) -> None:
    """Run the splash screen Qt application (blocking)."""
    import signal

    try:
        from PyQt5.QtWidgets import QApplication, QLabel
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
    except ImportError:
        print(f"[splash] PyQt5 not available — printing status: {status_text}", file=sys.stderr)
        # Block until SIGTERM
        signal.pause()
        return

    app = QApplication(sys.argv)

    label = QLabel(status_text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("background-color: black; color: white;")

    font = QFont("Sans", 28)
    label.setFont(font)

    label.showFullScreen()

    # Handle SIGTERM for clean DRM release
    def handle_sigterm(signum, frame):
        app.quit()

    signal.signal(signal.SIGTERM, handle_sigterm)

    sys.exit(app.exec_())


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "PiAuto"
    _run_splash_app(text)
