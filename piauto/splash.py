"""Splash screen application and subprocess manager.

Satisfies: FR-036 (splash in IDLE), FR-037 (status during connection).
See PiAuto-IG-001 §9.

This module serves two purposes:
1. When run as `python -m piauto.splash`, it displays a full-screen PyQt5
   EGLFS window that switches between views (idle splash, BT setup) based
   on commands received via stdin. A single Qt process holds DRM master for
   the entire session, preventing console flashes during view transitions.
2. When imported, it provides SplashManager for the state machine to manage
   the splash as a subprocess.

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


def detect_touchscreen_device() -> str:
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
    """Manages the splash screen subprocess.

    The splash is a single long-lived Qt process. View transitions
    (idle ↔ BT setup) are done by sending commands via stdin,
    avoiding DRM master release and console flashes.
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._line_queue: asyncio.Queue[str] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    def _build_env(self) -> dict[str, str]:
        """Build a minimal environment for the splash subprocess.

        Uses an explicit whitelist rather than inheriting all of os.environ so
        that sensitive variables present in the root process (credentials, tokens,
        piauto secrets) are not leaked into the Qt child process.
        """
        parent = os.environ
        env: dict[str, str] = {}

        # Essentials the child needs to run at all
        for key in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL"):
            if key in parent:
                env[key] = parent[key]

        # XDG runtime (needed by Qt/PipeWire)
        for key in ("XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
            if key in parent:
                env[key] = parent[key]

        # Library paths (non-standard Qt/GL installs)
        for key in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
            if key in parent:
                env[key] = parent[key]

        # Development safety valves — preserve PIAUTO_* overrides
        for key, val in parent.items():
            if key.startswith("PIAUTO_"):
                env[key] = val

        # Qt EGLFS display configuration
        env["QT_QPA_PLATFORM"] = "eglfs"
        env["QT_QPA_EGLFS_KMS_CONFIG"] = parent.get(
            "QT_QPA_EGLFS_KMS_CONFIG", "/data/eglfs.json"
        )
        env["QT_QPA_EGLFS_HIDECURSOR"] = "1"

        return env

    async def _ensure_running(self) -> None:
        """Ensure the splash process is running and the stdout reader is active."""
        if self.is_running:
            return

        cmd = [sys.executable, "-m", "piauto.splash"]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
            log.info("Splash process launched (pid %d)", self._proc.pid)
        except (FileNotFoundError, PermissionError) as exc:
            log.warning("Failed to launch splash: %s", exc)
            return

        self._reader_task = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self) -> None:
        """Read lines from splash stdout and put them in the queue.

        Runs as a background task for the lifetime of the splash process.
        Uses a single continuous readline() loop so no line is ever dropped,
        regardless of when state-machine callers check the queue.
        """
        if not self._proc or not self._proc.stdout:
            return
        try:
            while True:
                line_bytes = await self._proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode().strip()
                if line:
                    await self._line_queue.put(line)
        except (asyncio.CancelledError, OSError):
            pass

    async def _send_command(self, command: str) -> None:
        """Send a command to the splash process via stdin and flush."""
        if self._proc and self._proc.stdin and self._proc.returncode is None:
            try:
                self._proc.stdin.write(f"{command}\n".encode())
                await self._proc.stdin.drain()
            except (BrokenPipeError, OSError, ConnectionResetError):
                pass

    async def launch(self, status_text: str) -> None:
        """Show the idle splash screen with the given status text."""
        await self._ensure_running()
        await self._send_command(f"STATUS|{status_text}")

    async def launch_bt_setup(self) -> None:
        """Switch to the Bluetooth speaker pairing UI."""
        await self._ensure_running()
        await self._send_command("BT_SETUP")
        log.info("Switched to BT setup view")

    async def read_stdout_line(self) -> str | None:
        """Wait for the next line from the splash subprocess stdout.

        Blocks until a line is available in the queue.  Returns None only if
        the splash process has exited and the queue is empty.
        Used to receive signals like 'SETUP' or 'PAIRED|mac|name' from the UI.
        """
        if not self.is_running and self._line_queue.empty():
            return None
        try:
            return await self._line_queue.get()
        except asyncio.CancelledError:
            raise

    async def kill(self) -> None:
        """Stop the splash process and wait for it to exit.

        Sends a QUIT stdin command first (processed by the Qt event loop) so
        the app can exit cleanly and release DRM master without requiring the
        kernel SIGTERM signal path.  Falls back to SIGTERM then SIGKILL.
        """
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._reader_task = None

        if not self._proc or self._proc.returncode is not None:
            self._proc = None
            return

        log.debug("Stopping splash (pid %d)", self._proc.pid)

        # Ask Qt to quit gracefully via stdin — faster than the SIGTERM path
        # because the stdin notifier fires in the Qt event loop without a
        # signal-to-socket round trip.
        await self._send_command("QUIT")

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2)
            self._proc = None
            return
        except TimeoutError:
            pass

        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=3)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()
            log.warning("Splash killed (did not stop in 5 s)")

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
    "  background-color: #16213e; color: white; border: 2px solid #4a6fa5;"
    "  border-radius: 8px; padding: 10px; font-size: 16px;"
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
    "  background-color: #16213e; color: #aaa; border: 1px solid #4a6fa5;"
    "  border-radius: 6px; padding: 10px 24px; font-size: 18px;"
    "}"
    "QPushButton:pressed { background-color: #0f3460; color: white; }"
)


def _run_app() -> None:
    """Run the unified splash/setup Qt application (blocking).

    Listens on stdin for commands to switch views:
      STATUS|text  — show idle splash with status text
      BT_SETUP     — switch to BT speaker pairing UI
    Writes signals to stdout:
      SETUP        — user tapped Setup button
      BACK         — user tapped Back in BT setup
      PAIRED|mac|name — BT speaker paired successfully
    """
    import signal
    import socket

    try:
        from PyQt5.QtWidgets import (
            QApplication, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
            QPushButton, QScrollArea, QStackedWidget,
        )
        from PyQt5.QtCore import Qt, QProcess, QSocketNotifier
    except ImportError:
        print("[splash] PyQt5 not available", file=sys.stderr)
        signal.pause()
        return

    app = QApplication(sys.argv)

    # ── Main window with stacked views ──
    win = QWidget()
    win.setStyleSheet(_STYLE_BG)
    win_layout = QVBoxLayout(win)
    win_layout.setContentsMargins(0, 0, 0, 0)

    stack = QStackedWidget()
    win_layout.addWidget(stack)

    # ── View 0: Idle splash ──
    idle_page = QWidget()
    idle_page.setStyleSheet(_STYLE_BG)
    idle_layout = QVBoxLayout(idle_page)

    idle_layout.addStretch(1)

    idle_label = QLabel("PiAuto")
    idle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    idle_label.setStyleSheet(_STYLE_TITLE)
    idle_layout.addWidget(idle_label)

    idle_layout.addStretch(1)

    setup_btn = QPushButton("Setup")
    setup_btn.setStyleSheet(_STYLE_SETUP_BTN)
    setup_btn.setFixedHeight(52)
    setup_btn.setFixedWidth(140)
    setup_btn.clicked.connect(lambda: print("SETUP", flush=True))
    idle_layout.addWidget(setup_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    stack.addWidget(idle_page)

    # ── View 1: BT setup ──
    bt_page = QWidget()
    bt_page.setStyleSheet(_STYLE_BG)
    bt_layout = QVBoxLayout(bt_page)
    bt_layout.setContentsMargins(20, 15, 20, 15)

    # Title bar
    title_bar = QHBoxLayout()
    bt_title = QLabel("Bluetooth Speaker Setup")
    bt_title.setStyleSheet(_STYLE_TITLE)
    title_bar.addWidget(bt_title)
    title_bar.addStretch()

    back_btn = QPushButton("Back")
    back_btn.setStyleSheet(_STYLE_BTN)
    back_btn.setFixedSize(100, 44)
    title_bar.addWidget(back_btn)
    bt_layout.addLayout(title_bar)

    # Status label
    bt_status = QLabel("Press Scan to find Bluetooth speakers")
    bt_status.setStyleSheet(_STYLE_STATUS)
    bt_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
    bt_layout.addWidget(bt_status)

    # Scrollable device list
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    device_container = QWidget()
    device_container.setStyleSheet("background: transparent;")
    device_layout = QVBoxLayout(device_container)
    device_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(device_container)
    bt_layout.addWidget(scroll, stretch=1)

    # Scan button
    btn_bar = QHBoxLayout()
    scan_btn = QPushButton("Scan")
    scan_btn.setStyleSheet(_STYLE_BTN_ACCENT)
    scan_btn.setFixedHeight(50)
    btn_bar.addWidget(scan_btn)
    bt_layout.addLayout(btn_bar)

    stack.addWidget(bt_page)

    # ── BT scanning logic ──
    discovered: dict[str, str] = {}
    scan_process: list[QProcess] = []
    pair_process: list[QProcess] = []

    def _clear_devices():
        while device_layout.count():
            item = device_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_device_button(mac: str, name: str):
        btn = QPushButton(f"{name}  \u2014  {mac}")
        btn.setStyleSheet(_STYLE_BTN)
        btn.setFixedHeight(48)
        btn.clicked.connect(lambda checked, m=mac, n=name: _pair_device(m, n, btn))
        device_layout.addWidget(btn)

    def _scan():
        bt_status.setText("Scanning for speakers...")
        bt_status.setStyleSheet("color: #e94560; font-size: 16px;")
        scan_btn.setEnabled(False)
        _clear_devices()
        discovered.clear()

        for p in scan_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        scan_process.clear()

        proc = QProcess(win)
        scan_process.append(proc)

        def _on_scan_stdout():
            while proc.canReadLine():
                line = bytes(proc.readLine()).decode().strip()
                if line.startswith("DEVICE|"):
                    parts = line.split("|", 3)
                    if len(parts) >= 4:
                        mac, name = parts[1], parts[2]
                        if mac not in discovered:
                            discovered[mac] = name
                            _add_device_button(mac, name)
                elif line == "SCAN_DONE":
                    bt_status.setText(
                        f"Found {len(discovered)} device(s) \u2014 tap to pair"
                    )
                    bt_status.setStyleSheet(_STYLE_STATUS)
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
        bt_status.setText(f"Pairing with {name}...")
        bt_status.setStyleSheet("color: #e94560; font-size: 16px;")

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
                    bt_status.setText(f"Connected to {paired_name}!")
                    bt_status.setStyleSheet("color: #00ff88; font-size: 16px;")
                    btn.setStyleSheet(
                        "QPushButton { background-color: #00662a; color: white;"
                        "  border: 2px solid #00ff88; border-radius: 8px;"
                        "  padding: 12px; font-size: 20px; }"
                    )
                    print(f"PAIRED|{paired_mac}|{paired_name}", flush=True)
                elif line.startswith("PAIR_FAIL|"):
                    msg = line[len("PAIR_FAIL|"):]
                    bt_status.setText(f"Failed: {msg}")
                    bt_status.setStyleSheet("color: #ff4444; font-size: 16px;")
                    btn.setStyleSheet(_STYLE_BTN)

        proc.readyReadStandardOutput.connect(_on_pair_stdout)
        # Run as pi user — pairing as root fails (discovery can't find devices)
        proc.start("sudo", ["-u", "pi", sys.executable, "-m", "piauto.bt_pair", "pair", mac])

    scan_btn.clicked.connect(_scan)

    def _show_bt_setup():
        bt_status.setText("Press Scan to find Bluetooth speakers")
        bt_status.setStyleSheet(_STYLE_STATUS)
        scan_btn.setEnabled(True)
        _clear_devices()
        discovered.clear()
        stack.setCurrentIndex(1)

    def _show_idle():
        # Kill any running BT processes
        for p in scan_process + pair_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        stack.setCurrentIndex(0)

    back_btn.clicked.connect(lambda: (print("BACK", flush=True), _show_idle()))

    # ── stdin command reader ──
    # Use raw fd reads to avoid Python buffered I/O conflicts with QSocketNotifier.
    # Python's sys.stdin.readline() can consume all fd data into its internal buffer,
    # causing QSocketNotifier to never fire again for remaining lines.
    import fcntl
    stdin_fd = sys.stdin.fileno()
    fcntl.fcntl(stdin_fd, fcntl.F_SETFL,
                fcntl.fcntl(stdin_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
    stdin_notifier = QSocketNotifier(stdin_fd, QSocketNotifier.Type.Read, win)
    _stdin_buf = [""]  # mutable container for nonlocal access

    def _on_stdin():
        try:
            data = os.read(stdin_fd, 4096)
        except (OSError, BlockingIOError):
            return
        if not data:
            return
        _stdin_buf[0] += data.decode("utf-8", errors="replace")
        while "\n" in _stdin_buf[0]:
            line, _stdin_buf[0] = _stdin_buf[0].split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("STATUS|"):
                text = line[len("STATUS|"):]
                idle_label.setText(text)
                _show_idle()
            elif line == "BT_SETUP":
                _show_bt_setup()
            elif line == "QUIT":
                _on_sigterm()

    stdin_notifier.activated.connect(_on_stdin)

    # ── SIGTERM handler ──
    sig_r, sig_w = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    sig_r.setblocking(False)
    sig_w.setblocking(False)

    def handle_sigterm(signum, frame):
        sig_w.send(b"\x00")

    def _on_sigterm():
        for p in scan_process + pair_process:
            if p.state() != QProcess.ProcessState.NotRunning:
                p.kill()
        app.quit()

    sig_notifier = QSocketNotifier(sig_r.fileno(), QSocketNotifier.Type.Read, win)
    sig_notifier.activated.connect(_on_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    # ── Start ──
    # Set initial status from command line arg if provided
    if len(sys.argv) > 1:
        idle_label.setText(sys.argv[1])

    stack.setCurrentIndex(0)
    win.showFullScreen()

    sys.exit(app.exec_())


if __name__ == "__main__":
    _run_app()
