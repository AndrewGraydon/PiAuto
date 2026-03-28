"""OpenAuto process launcher and monitor.

Satisfies: FR-011 to FR-014 (TCP/TLS, AA negotiation via OpenAuto),
           FR-016 to FR-019 (video), FR-020 to FR-031 (audio, touch).
See PiAuto-IG-001 §10.

OpenAuto is an external C++ binary that handles the entire AA session.
This module launches it as a subprocess, monitors its lifecycle,
and parses its output for state transition events.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from piauto.config import OpenAutoConfig
from piauto.log import get_logger
from piauto.splash import detect_touchscreen_device

log = get_logger("openauto")

TLS_CERT_DIR = Path("/data/tls")
TLS_KEY = TLS_CERT_DIR / "key.pem"
TLS_CERT = TLS_CERT_DIR / "cert.pem"

# Patterns in OpenAuto's log output indicating projection is active.
# Based on OpenAuto source: VideoMediaSinkService logs when video starts.
PROJECTION_ACTIVE_PATTERNS = [
    "[VideoMediaSinkService] start()",
    "[VideoService] start",
    "Projection started",
    "MediaSink started",
]

# Pattern indicating OpenAuto has quit or the phone disconnected.
PROJECTION_STOPPED_PATTERNS = [
    "[WifiProjectionService] stop()",
    "onAndroidAutoQuit",
]


async def ensure_tls_cert() -> bool:
    """Generate a self-signed TLS certificate if one doesn't exist.

    Uses ECDSA P-256 with 100-year validity per PiAuto-IG-001 §8.
    """
    if TLS_CERT.exists() and TLS_KEY.exists():
        log.debug("TLS certificate already exists at %s", TLS_CERT)
        return True

    log.info("Generating self-signed TLS certificate...")
    TLS_CERT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "openssl", "req", "-x509",
            "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", str(TLS_KEY),
            "-out", str(TLS_CERT),
            "-days", "36500",
            "-nodes",
            "-subj", "/CN=PiAuto",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            log.error("TLS cert generation failed: %s", stderr.decode().strip())
            return False

        # Restrict key file permissions
        os.chmod(TLS_KEY, 0o600)
        log.info("TLS certificate generated: %s", TLS_CERT)
        return True

    except FileNotFoundError:
        log.error("openssl not found — cannot generate TLS certificate")
        return False


async def _kill_stale_autoapp() -> None:
    """Kill any stale autoapp/openauto processes that might hold port 5000."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-9", "-f", "autoapp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        # Brief delay for port release
        await asyncio.sleep(0.5)
    except FileNotFoundError:
        pass


class OpenAutoManager:
    """Manages the OpenAuto C++ process lifecycle."""

    def __init__(self, config: OpenAutoConfig) -> None:
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._projection_active = asyncio.Event()
        self._projection_stopped = asyncio.Event()
        self._monitor_task: asyncio.Task | None = None

    async def launch(self) -> bool:
        """Launch OpenAuto as a subprocess. Returns True if started successfully."""
        # Kill any previous instance that might still hold the TCP port
        if self._proc and self._proc.returncode is None:
            await self.kill()

        # Also kill any stale autoapp processes from outside our management
        await _kill_stale_autoapp()

        binary = self._config.binary

        if not Path(binary).exists():
            log.error("OpenAuto binary not found: %s", binary)
            return False

        cmd = [binary] + self._config.extra_args
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "eglfs",
            "QT_QPA_EGLFS_KMS_CONFIG": os.environ.get(
                "QT_QPA_EGLFS_KMS_CONFIG", "/data/eglfs.json"
            ),
            "QT_QPA_EGLFS_HIDECURSOR": "1",
            # Disable libinput — it opens the touchscreen as both pointer
            # and touch device, causing double-tap behaviour.  We use the
            # explicit evdevtouch plugin instead.
            "QT_QPA_EGLFS_NO_LIBINPUT": "1",
            "QT_QPA_GENERIC_PLUGINS": "evdevtouch",
            "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS": os.environ.get(
                "QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS",
                detect_touchscreen_device() + ":grab",
            ),
            # PipeWire/PulseAudio runs as user pi (uid 1000) — autoapp
            # runs as root but needs access to the PipeWire PulseAudio socket
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "PULSE_SERVER": "unix:/run/user/1000/pulse/native",
        }

        try:
            # cwd=/data so OpenAuto reads /data/openauto.ini
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd="/data",
            )
            log.info("OpenAuto launched (pid %d): %s", self._proc.pid, " ".join(cmd))
        except (FileNotFoundError, PermissionError) as exc:
            log.error("Failed to launch OpenAuto: %s", exc)
            return False

        # Start background tasks to parse output for projection status
        self._projection_active.clear()
        self._projection_stopped.clear()
        self._monitor_task = asyncio.create_task(self._monitor_output())
        self._stdout_task = asyncio.create_task(self._monitor_stdout())

        return True

    async def _monitor_stdout(self) -> None:
        """Read OpenAuto's stdout for additional log output."""
        if not self._proc or not self._proc.stdout:
            return

        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    log.debug("OpenAuto(stdout): %s", text)
                    for pattern in PROJECTION_ACTIVE_PATTERNS:
                        if pattern in text:
                            log.info("Projection active detected (stdout): %s", text)
                            self._projection_active.set()
                            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("stdout monitor failed: %s", exc)

    async def _monitor_output(self) -> None:
        """Read OpenAuto's stderr line by line, looking for projection status.

        OpenAuto uses Boost.Log which writes to stderr by default.
        """
        if not self._proc or not self._proc.stderr:
            return

        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break  # process exited

                text = line.decode(errors="replace").strip()
                if text:
                    log.debug("OpenAuto: %s", text)

                for pattern in PROJECTION_ACTIVE_PATTERNS:
                    if pattern in text:
                        log.info("Projection active detected: %s", text)
                        self._projection_active.set()
                        break

                for pattern in PROJECTION_STOPPED_PATTERNS:
                    if pattern in text:
                        log.info("Projection stopped detected: %s", text)
                        self._projection_stopped.set()
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("stderr monitor failed: %s", exc)

    async def wait_for_listening(self, timeout: float = 15.0) -> bool:
        """Wait for autoapp to start listening on its TCP port (5000).

        Polls `ss` every 0.5s. Returns True once port is bound, False on timeout.
        """
        try:
            async with asyncio.timeout(timeout):
                while True:
                    if self._proc and self._proc.returncode is not None:
                        log.warning("OpenAuto exited before binding port")
                        return False
                    proc = await asyncio.create_subprocess_exec(
                        "ss", "-tlnH",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await proc.communicate()
                    if b":5000 " in stdout or b":5000\t" in stdout:
                        log.info("OpenAuto is listening on port 5000")
                        return True
                    await asyncio.sleep(0.5)
        except TimeoutError:
            log.warning("OpenAuto did not bind port 5000 within %.0f s", timeout)
            return False

    async def wait_for_ready(self, timeout: float = 30.0) -> bool:
        """Wait for OpenAuto to report projection is active.

        Returns True if projection started, False on timeout or process exit.
        """
        if not self._proc:
            return False

        try:
            async with asyncio.timeout(timeout):
                # Race between projection active and process exit
                ready_task = asyncio.create_task(self._projection_active.wait())
                exit_task = asyncio.create_task(self._proc.wait())

                done, pending = await asyncio.wait(
                    [ready_task, exit_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if ready_task in done:
                    return True

                # Process exited before becoming ready
                code = self._proc.returncode
                log.warning("OpenAuto exited before projection active (code %s)", code)
                return False

        except TimeoutError:
            log.warning("OpenAuto did not become ready within %.0f s", timeout)
            return False

    async def wait_for_projection_stopped(self) -> bool:
        """Wait for OpenAuto to report projection stopped (phone disconnected).

        Returns True if projection stopped, False if process exited first.
        """
        if not self._proc:
            return False

        stopped_task = asyncio.create_task(self._projection_stopped.wait())
        exit_task = asyncio.create_task(self._proc.wait())

        done, pending = await asyncio.wait(
            [stopped_task, exit_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        return stopped_task in done

    async def wait_for_exit(self) -> int | None:
        """Wait for OpenAuto to exit. Returns the exit code."""
        if not self._proc:
            return None

        code = await self._proc.wait()
        await self._cancel_monitors()
        log.info("OpenAuto exited with code %s", code)
        return code

    async def kill(self) -> None:
        """Send SIGTERM to OpenAuto and wait for it to exit."""
        if not self._proc or self._proc.returncode is not None:
            return

        log.info("Sending SIGTERM to OpenAuto (pid %d)", self._proc.pid)
        self._proc.terminate()

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except TimeoutError:
            log.warning("OpenAuto did not exit after SIGTERM, sending SIGKILL")
            self._proc.kill()
            await self._proc.wait()

        await self._cancel_monitors()

    async def _cancel_monitors(self) -> None:
        """Cancel output monitoring tasks."""
        for task in (self._monitor_task, getattr(self, "_stdout_task", None)):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._monitor_task = None
        self._stdout_task = None

    @property
    def is_running(self) -> bool:
        """Check if OpenAuto process is currently running."""
        return self._proc is not None and self._proc.returncode is None

    @property
    def returncode(self) -> int | None:
        """Get the exit code, or None if still running."""
        return self._proc.returncode if self._proc else None
