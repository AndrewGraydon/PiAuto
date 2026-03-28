"""PiAuto state machine — the central orchestrator.

Implements PiAuto-SM-001 v3.0. Every runtime behavior is driven by this
state machine. It manages the connection lifecycle (BLE → WiFi → OpenAuto)
and monitors GPIO 17 for ignition-off events.

States: BOOTING, IDLE, BT_PAIRING, WIFI_WAIT, TCP_CONNECT,
        PROJECTION_ACTIVE, ERROR_RECOVERY, SHUTDOWN.

Global invariants (SM-001 §5):
1. IgnitionOff is always handled — every state transitions to SHUTDOWN.
2. Single active state at any time.
3. No implicit transitions — every change is triggered by an explicit event.
4. ERROR_RECOVERY is only reachable from TCP_CONNECT and PROJECTION_ACTIVE.
5. Display ownership — exactly one of splash/OpenAuto owns DRM master.
6. OpenAuto autonomy — state machine only monitors, does not interfere.
"""

from __future__ import annotations

import asyncio
import enum
import os
import subprocess
import time

from piauto.ble import BleManager, PhoneInfo
from piauto.clock import restore_time, save_time
from piauto.config import PiAutoConfig, load_config
from piauto.gpio import GpioManager
from piauto.log import get_logger, setup_logging
from piauto.openauto import OpenAutoManager, ensure_tls_cert
from piauto.splash import SplashManager, write_eglfs_config
from piauto.thermal import ThermalMonitor
from piauto.volume import VolumeSyncManager
from piauto.wifi import WifiManager

log = get_logger("statemachine")


class State(enum.Enum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    BT_PAIRING = "BT_PAIRING"
    WIFI_WAIT = "WIFI_WAIT"
    TCP_CONNECT = "TCP_CONNECT"
    PROJECTION_ACTIVE = "PROJECTION_ACTIVE"
    ERROR_RECOVERY = "ERROR_RECOVERY"
    SHUTDOWN = "SHUTDOWN"


class Event(enum.Enum):
    SERVICES_STARTED = "ServicesStarted"
    BOOT_FAILED = "BootFailed"
    BOOT_TIMEOUT = "BootTimeout"
    PHONE_DETECTED = "PhoneDetected"
    CREDENTIALS_SENT = "CredentialsSent"
    BT_HANDSHAKE_FAILED = "BtHandshakeFailed"
    PHONE_JOINED_AP = "PhoneJoinedAP"
    WIFI_TIMEOUT = "WifiTimeout"
    OPENAUTO_READY = "OpenAutoReady"
    OPENAUTO_FAILED = "OpenAutoFailed"
    CONNECTION_LOST = "ConnectionLost"
    PHONE_DISCONNECTED = "PhoneDisconnected"
    RETRY_AVAILABLE = "RetryAvailable"
    RETRIES_EXHAUSTED = "RetriesExhausted"
    IGNITION_OFF = "IgnitionOff"


# Timeouts per SM-001 §6
BOOT_TIMEOUT_S = 60
BT_PAIRING_TIMEOUT_S = 30  # includes AP startup + RFCOMM credential exchange
WIFI_WAIT_TIMEOUT_S = 30
TCP_CONNECT_TIMEOUT_S = 30
ERROR_RECOVERY_WAIT_S = 5
MAX_RETRIES = 3


class StateMachine:
    """The PiAuto state machine orchestrator."""

    def __init__(self) -> None:
        self._state = State.BOOTING
        self._config: PiAutoConfig | None = None
        self._gpio: GpioManager | None = None
        self._thermal: ThermalMonitor | None = None
        self._thermal_task: asyncio.Task | None = None
        self._ble: BleManager | None = None
        self._wifi: WifiManager | None = None
        self._openauto: OpenAutoManager | None = None
        self._splash: SplashManager | None = None
        self._volume: VolumeSyncManager | None = None
        self._ignition_task: asyncio.Task | None = None
        self._ignition_off = asyncio.Event()
        self._retry_count = 0
        self._current_phone: PhoneInfo | None = None
        self._running = False

    @property
    def state(self) -> State:
        return self._state

    def _transition(self, new_state: State, event: Event) -> None:
        """Log and execute a state transition."""
        old = self._state
        self._state = new_state
        log.info("Transition: %s → %s (event: %s)", old.value, new_state.value, event.value)

    def run(self) -> None:
        """Entry point — run the state machine event loop (blocking)."""
        setup_logging()
        log.info("PiAuto starting...")
        asyncio.run(self._main_loop())

    async def _main_loop(self) -> None:
        """Async main loop — processes states sequentially."""
        self._running = True

        while self._running:
            try:
                match self._state:
                    case State.BOOTING:
                        await self._handle_booting()
                    case State.IDLE:
                        await self._handle_idle()
                    case State.BT_PAIRING:
                        await self._handle_bt_pairing()
                    case State.WIFI_WAIT:
                        await self._handle_wifi_wait()
                    case State.TCP_CONNECT:
                        await self._handle_tcp_connect()
                    case State.PROJECTION_ACTIVE:
                        await self._handle_projection_active()
                    case State.ERROR_RECOVERY:
                        await self._handle_error_recovery()
                    case State.SHUTDOWN:
                        await self._handle_shutdown()
                        return  # exit the loop

            except asyncio.CancelledError:
                log.info("State machine cancelled")
                return
            except Exception:
                log.exception("Unhandled error in state %s", self._state.value)
                self._transition(State.SHUTDOWN, Event.BOOT_FAILED)

    # ── BOOTING ──────────────────────────────────────────────

    async def _handle_booting(self) -> None:
        """BOOTING: Initialize all subsystems. SM-001 §3.1."""
        log.info("=== BOOTING ===")

        try:
            async with asyncio.timeout(BOOT_TIMEOUT_S):
                # Load configuration
                self._config = load_config()

                # Restore system clock
                restore_time()

                # Ensure TLS certificate exists
                await ensure_tls_cert()

                # Initialize GPIO
                self._gpio = GpioManager()
                await self._gpio.setup()

                # Start ignition monitoring
                self._ignition_off.clear()
                self._ignition_task = asyncio.create_task(
                    self._gpio.monitor_ignition(
                        callback=self._on_ignition_off,
                        debounce_ms=self._config.power.ignition_debounce_ms,
                    )
                )

                # Start thermal monitoring
                self._thermal = ThermalMonitor(self._config.thermal, self._gpio)
                self._thermal_task = asyncio.create_task(self._thermal.run())

                # Detect DRM card and write EGLFS config
                # (Pi 4 card0/card1 can swap between reboots)
                write_eglfs_config()

                # Initialize splash screen
                self._splash = SplashManager()
                await self._splash.launch("Starting...")

                # Initialize WiFi manager first (does not start AP yet)
                # — BLE needs the AP IP for credential exchange
                self._wifi = WifiManager(self._config.wifi)

                # Initialize BLE with AP endpoint info
                self._ble = BleManager(
                    self._config.bluetooth, self._config.wifi,
                    ap_ip=self._wifi.ap_ip, ap_interface=self._wifi.ap_interface,
                )
                await self._ble.setup()

                # Initialize OpenAuto manager
                self._openauto = OpenAutoManager(self._config.openauto)

                # Initialize AVRCP volume sync
                self._volume = VolumeSyncManager()

        except TimeoutError:
            log.error("Boot timeout — services not ready within %d s", BOOT_TIMEOUT_S)
            self._transition(State.SHUTDOWN, Event.BOOT_TIMEOUT)
            return
        except Exception:
            log.exception("Boot failed")
            self._transition(State.SHUTDOWN, Event.BOOT_FAILED)
            return

        log.info("All services initialized")
        self._transition(State.IDLE, Event.SERVICES_STARTED)

    # ── IDLE ─────────────────────────────────────────────────

    async def _handle_idle(self) -> None:
        """IDLE: Advertise BLE, wait for phone. SM-001 §3.2."""
        log.info("=== IDLE ===")

        # Reset retry counter on entering IDLE
        self._retry_count = 0
        self._current_phone = None

        # Entry actions: stop AP and OpenAuto if running
        await self._wifi.stop_ap()
        if self._openauto and self._openauto.is_running:
            await self._openauto.kill()

        # Ensure splash is showing
        await self._splash.launch("Waiting for phone...")

        # Start BLE advertising
        await self._ble.start_advertising()

        # Monitor splash stdout for "SETUP" button press
        async def _watch_splash_stdout():
            while True:
                line = await self._splash.read_stdout_line()
                if line == "SETUP":
                    return "SETUP"
                if line is None:
                    # Splash exited or no more output
                    await asyncio.sleep(0.5)

        # Wait for phone, ignition off, or setup button
        phone_task = asyncio.create_task(self._ble.wait_for_phone())
        ignition_task = asyncio.create_task(self._ignition_off.wait())
        setup_task = asyncio.create_task(_watch_splash_stdout())

        done, pending = await asyncio.wait(
            [phone_task, ignition_task, setup_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if ignition_task in done:
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        if setup_task in done:
            # Wait for other tasks to actually cancel before reading from
            # splash stdout in _handle_bt_setup (avoids concurrent readers)
            for task in pending:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            log.info("Setup requested — launching BT speaker pairing UI")
            await self._ble.stop_advertising()
            await self._handle_bt_setup()
            return

        # Wait for cancelled tasks to release resources (especially splash stdout)
        for task in pending:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        phone = phone_task.result()
        if phone:
            self._current_phone = phone
            log.info("Phone detected: %s (%s)", phone.name, phone.mac)
            self._transition(State.BT_PAIRING, Event.PHONE_DETECTED)
        else:
            # Shouldn't happen unless cancelled — stay in IDLE
            log.debug("Phone detection returned None — remaining in IDLE")

    async def _handle_bt_setup(self) -> None:
        """Run the BT speaker pairing UI, then return to IDLE."""
        await self._splash.launch_bt_setup()
        log.info("BT setup UI active — waiting for user to finish")

        # Read stdout lines from the UI until it exits or user presses Back
        while self._splash.is_running:
            line = await self._splash.read_stdout_line()
            if line is None:
                await asyncio.sleep(0.5)
                continue
            if line == "BACK":
                log.info("BT setup: user pressed Back")
                break
            if line.startswith("PAIRED|"):
                parts = line.split("|", 2)
                if len(parts) == 3:
                    mac, name = parts[1], parts[2]
                    log.info("BT speaker paired: %s (%s)", name, mac)

        # Return to IDLE
        self._transition(State.IDLE, Event.SERVICES_STARTED)

    # ── BT_PAIRING ───────────────────────────────────────────

    async def _handle_bt_pairing(self) -> None:
        """BT_PAIRING: Exchange WiFi credentials. SM-001 §3.3.

        Critical timing: autoapp takes ~6s to bind port 5000. The phone
        tries to TCP-connect immediately after receiving credentials.
        So we must: start AP → launch autoapp → wait for port ready →
        THEN send credentials via RFCOMM.
        """
        log.info("=== BT_PAIRING ===")

        await self._splash.launch("Pairing...")

        try:
            async with asyncio.timeout(BT_PAIRING_TIMEOUT_S):
                if self._ignition_off.is_set():
                    self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
                    return

                # 1. Start WiFi AP
                if not await self._wifi.start_ap():
                    log.error("Failed to start AP during BT_PAIRING")
                    self._transition(State.IDLE, Event.BT_HANDSHAKE_FAILED)
                    return

                # 2. Kill splash and launch autoapp so it's listening on
                #    port 5000 BEFORE the phone gets the IP:port
                await self._splash.kill_and_wait_drm_release()
                if not await self._openauto.launch():
                    log.error("Failed to launch OpenAuto during BT_PAIRING")
                    self._transition(State.IDLE, Event.BT_HANDSHAKE_FAILED)
                    return

                # 3. Wait for autoapp to bind port 5000 (polls every 0.5s)
                if not await self._openauto.wait_for_listening(timeout=15.0):
                    log.error("OpenAuto did not start listening in time")
                    await self._openauto.kill()
                    self._transition(State.IDLE, Event.BT_HANDSHAKE_FAILED)
                    return

                # 4. NOW send credentials — phone will TCP-connect to
                #    the already-listening autoapp
                success = await self._ble.send_credentials(self._current_phone)

                if success:
                    self._ble.save_pairing(self._current_phone, time.time())
                    await self._ble.stop_advertising()
                    self._transition(State.WIFI_WAIT, Event.CREDENTIALS_SENT)
                else:
                    log.warning("BLE handshake failed")
                    await self._openauto.kill()
                    self._transition(State.IDLE, Event.BT_HANDSHAKE_FAILED)

        except TimeoutError:
            log.warning("BT pairing timeout (%d s)", BT_PAIRING_TIMEOUT_S)
            if self._openauto and self._openauto.is_running:
                await self._openauto.kill()
            self._transition(State.IDLE, Event.BT_HANDSHAKE_FAILED)

    # ── WIFI_WAIT ────────────────────────────────────────────

    async def _handle_wifi_wait(self) -> None:
        """WIFI_WAIT: Wait for phone to join AP. SM-001 §3.4.

        AP and autoapp are already started in BT_PAIRING. autoapp is
        listening on port 5000. We just need to wait for the phone to
        join WiFi, then transition to TCP_CONNECT.
        """
        log.info("=== WIFI_WAIT ===")

        # Wait for phone to join AP or ignition off
        client_task = asyncio.create_task(
            self._wifi.wait_for_client(timeout=WIFI_WAIT_TIMEOUT_S)
        )
        ignition_task = asyncio.create_task(self._ignition_off.wait())

        done, pending = await asyncio.wait(
            [client_task, ignition_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if ignition_task in done:
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        if client_task.result():
            # Phone is on AP — autoapp already running and listening
            self._transition(State.TCP_CONNECT, Event.PHONE_JOINED_AP)
        else:
            # Timeout — no client joined
            await self._openauto.kill()
            await self._wifi.stop_ap()
            self._transition(State.IDLE, Event.WIFI_TIMEOUT)

    # ── TCP_CONNECT ──────────────────────────────────────────

    async def _handle_tcp_connect(self) -> None:
        """TCP_CONNECT: OpenAuto running, waiting for projection. SM-001 §3.5."""
        log.info("=== TCP_CONNECT ===")

        # Check ignition
        if self._ignition_off.is_set():
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        # Wait for OpenAuto to report projection active
        ready_task = asyncio.create_task(
            self._openauto.wait_for_ready(timeout=TCP_CONNECT_TIMEOUT_S)
        )
        ignition_task = asyncio.create_task(self._ignition_off.wait())

        done, pending = await asyncio.wait(
            [ready_task, ignition_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if ignition_task in done:
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        if ready_task.result():
            self._transition(State.PROJECTION_ACTIVE, Event.OPENAUTO_READY)
        else:
            log.warning("OpenAuto failed to reach projection active")
            self._transition(State.ERROR_RECOVERY, Event.OPENAUTO_FAILED)

    # ── PROJECTION_ACTIVE ────────────────────────────────────

    async def _handle_projection_active(self) -> None:
        """PROJECTION_ACTIVE: Monitor OpenAuto process. SM-001 §3.6."""
        log.info("=== PROJECTION_ACTIVE ===")

        # Start AVRCP volume sync (phone volume → PipeWire)
        if self._volume:
            self._volume.start()

        # Wait for OpenAuto to exit, projection stopped, or ignition off.
        # The phone can disconnect without autoapp exiting — it stays running
        # showing its own "waiting" screen. We detect this via stderr patterns
        # and kill autoapp ourselves to return to our splash.
        exit_task = asyncio.create_task(self._openauto.wait_for_exit())
        stopped_task = asyncio.create_task(
            self._openauto.wait_for_projection_stopped()
        )
        ignition_task = asyncio.create_task(self._ignition_off.wait())

        done, pending = await asyncio.wait(
            [exit_task, stopped_task, ignition_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        # Stop volume sync when leaving PROJECTION_ACTIVE
        if self._volume:
            self._volume.stop()

        if ignition_task in done:
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        if stopped_task in done:
            # Phone disconnected but autoapp still running — kill it
            # and return to splash
            log.info("Phone disconnected (projection stopped) — returning to IDLE")
            await self._openauto.kill()
            await self._splash.launch("Waiting for phone...")
            self._transition(State.IDLE, Event.PHONE_DISCONNECTED)
            return

        exit_code = exit_task.result()

        if exit_code == 0:
            # Clean phone disconnect (autoapp exited on its own)
            log.info("Phone disconnected cleanly")
            await self._splash.launch("Waiting for phone...")
            self._transition(State.IDLE, Event.PHONE_DISCONNECTED)
        else:
            # Connection lost or error
            log.warning("OpenAuto exited with code %s — connection lost", exit_code)
            self._transition(State.ERROR_RECOVERY, Event.CONNECTION_LOST)

    # ── ERROR_RECOVERY ───────────────────────────────────────

    async def _handle_error_recovery(self) -> None:
        """ERROR_RECOVERY: Retry TCP_CONNECT up to 3 times. SM-001 §3.7."""
        self._retry_count += 1
        log.info("=== ERROR_RECOVERY === (attempt %d/%d)", self._retry_count, MAX_RETRIES)

        # Show error status on splash
        await self._splash.launch(f"Reconnecting... ({self._retry_count}/{MAX_RETRIES})")

        # Check ignition
        if self._ignition_off.is_set():
            self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
            return

        # Wait before retrying
        try:
            async with asyncio.timeout(ERROR_RECOVERY_WAIT_S):
                await self._ignition_off.wait()
                # Ignition went off during wait
                self._transition(State.SHUTDOWN, Event.IGNITION_OFF)
                return
        except TimeoutError:
            pass  # Expected — the wait period elapsed normally

        if self._retry_count >= MAX_RETRIES:
            log.warning("Retries exhausted (%d/%d) — returning to IDLE", self._retry_count, MAX_RETRIES)
            await self._wifi.stop_ap()
            self._transition(State.IDLE, Event.RETRIES_EXHAUSTED)
        else:
            # Kill splash, relaunch OpenAuto for retry
            await self._splash.kill_and_wait_drm_release()
            if await self._openauto.launch():
                self._transition(State.TCP_CONNECT, Event.RETRY_AVAILABLE)
            else:
                log.error("Failed to relaunch OpenAuto")
                await self._wifi.stop_ap()
                self._transition(State.IDLE, Event.RETRIES_EXHAUSTED)

    # ── SHUTDOWN ─────────────────────────────────────────────

    async def _handle_shutdown(self) -> None:
        """SHUTDOWN: Clean shutdown sequence. SM-001 §3.8."""
        log.info("=== SHUTDOWN ===")

        # Kill OpenAuto if running
        if self._openauto and self._openauto.is_running:
            await self._openauto.kill()

        # Kill splash if running
        if self._splash and self._splash.is_running:
            await self._splash.kill()

        # Stop AP
        if self._wifi:
            await self._wifi.stop_ap()

        # Stop BLE
        if self._ble:
            await self._ble.stop_advertising()
            await self._ble.close()

        # Stop thermal monitoring
        if self._thermal:
            self._thermal.stop()
        if self._thermal_task:
            self._thermal_task.cancel()
            try:
                await self._thermal_task
            except asyncio.CancelledError:
                pass

        # Stop ignition monitoring
        if self._gpio:
            self._gpio.stop()
        if self._ignition_task:
            self._ignition_task.cancel()
            try:
                await self._ignition_task
            except asyncio.CancelledError:
                pass

        # Release GPIO
        if self._gpio:
            self._gpio.close()

        # Save system time
        save_time()

        self._running = False

        # Execute system shutdown (skip if PIAUTO_NO_HALT is set — for testing)
        if os.environ.get("PIAUTO_NO_HALT"):
            log.info("Shutdown complete — PIAUTO_NO_HALT set, not halting system")
        else:
            log.info("Shutdown complete — issuing system halt")
            try:
                subprocess.run(["shutdown", "-h", "now"], check=False)
            except FileNotFoundError:
                log.info("'shutdown' not available (dev machine?) — exiting normally")

    # ── Ignition callback ────────────────────────────────────

    def _on_ignition_off(self) -> None:
        """Called by GPIO manager when ignition-off is detected."""
        log.info("Ignition OFF detected")
        self._ignition_off.set()
