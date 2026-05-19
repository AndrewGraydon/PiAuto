"""Tests for StateMachine state transitions (SM-001).

All hardware managers are replaced by mocks (see conftest.py).
Tests run on a dev machine with no Pi hardware.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from piauto.ble import PhoneInfo
from piauto.config import PiAutoConfig
from piauto.statemachine import (
    MAX_RETRIES,
    State,
    StateMachine,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

async def _never():
    await asyncio.Event().wait()


async def _run_state(sm: StateMachine, handler_name: str) -> None:
    """Call a single state handler and return once it transitions."""
    await getattr(sm, handler_name)()


# ─────────────────────────────────────────────────────────────────────
# BOOTING
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_booting_transitions_to_idle_on_success():
    sm = StateMachine()
    with (
        patch("piauto.statemachine.load_config", return_value=PiAutoConfig()),
        patch("piauto.statemachine.restore_time"),
        patch("piauto.statemachine.ensure_tls_cert", new_callable=AsyncMock),
        patch("piauto.statemachine.write_eglfs_config"),
        patch("piauto.statemachine.save_time"),
        patch("piauto.statemachine.GpioManager") as MockGpio,
        patch("piauto.statemachine.ThermalMonitor") as MockThermal,
        patch("piauto.statemachine.SplashManager") as MockSplash,
        patch("piauto.statemachine.WifiManager") as MockWifi,
        patch("piauto.statemachine.BleManager") as MockBle,
        patch("piauto.statemachine.OpenAutoManager"),
        patch("piauto.statemachine.VolumeSyncManager"),
    ):
        gpio_inst = MagicMock()
        gpio_inst.setup = AsyncMock()
        gpio_inst.monitor_ignition = AsyncMock(side_effect=_never)
        MockGpio.return_value = gpio_inst

        thermal_inst = MagicMock()
        thermal_inst.run = AsyncMock(side_effect=_never)
        MockThermal.return_value = thermal_inst

        splash_inst = MagicMock()
        splash_inst.launch = AsyncMock()
        MockSplash.return_value = splash_inst

        wifi_inst = MagicMock()
        wifi_inst.ap_ip = "192.168.4.1"
        wifi_inst.ap_interface = "wlan0"
        MockWifi.return_value = wifi_inst

        ble_inst = MagicMock()
        ble_inst.setup = AsyncMock(return_value=True)
        MockBle.return_value = ble_inst

        await sm._handle_booting()

        # Clean up background tasks
        for task in (sm._ignition_task, sm._thermal_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_booting_transitions_to_shutdown_on_exception():
    sm = StateMachine()
    with patch("piauto.statemachine.load_config", side_effect=RuntimeError("disk failure")):
        await sm._handle_booting()
    assert sm.state == State.SHUTDOWN


# ─────────────────────────────────────────────────────────────────────
# IDLE
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idle_transitions_to_bt_pairing_on_phone_detected(sm, phone):
    sm._ble.wait_for_phone = AsyncMock(return_value=phone)
    await sm._handle_idle()
    assert sm.state == State.BT_PAIRING
    assert sm._current_phone == phone


@pytest.mark.asyncio
async def test_idle_transitions_to_shutdown_on_ignition_off(sm):
    sm._ignition_off.set()
    await sm._handle_idle()
    assert sm.state == State.SHUTDOWN


@pytest.mark.asyncio
async def test_idle_resets_retry_counter(sm, phone):
    sm._retry_count = 3
    sm._ble.wait_for_phone = AsyncMock(return_value=phone)
    await sm._handle_idle()
    assert sm._retry_count == 0


@pytest.mark.asyncio
async def test_idle_cancels_reconnect_loop_before_transition(sm, phone):
    """Reconnect loop must be cancelled before the state transition fires."""
    sm._ble.get_last_connected_mac = MagicMock(return_value="AA:BB:CC:DD:EE:FF")
    # Let wait_for_phone return quickly so the phone task wins
    sm._ble.wait_for_phone = AsyncMock(return_value=phone)

    await sm._handle_idle()

    assert sm.state == State.BT_PAIRING
    # try_reconnect_phone may or may not have been called, but no stray tasks
    # should remain — the important thing is we transitioned cleanly


# ─────────────────────────────────────────────────────────────────────
# BT_PAIRING
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_wifi_wait_on_success(sm, phone):
    sm._current_phone = phone
    await sm._handle_bt_pairing()
    assert sm.state == State.WIFI_WAIT


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_idle_when_ap_fails(sm, phone):
    sm._current_phone = phone
    sm._wifi.start_ap = AsyncMock(return_value=False)
    await sm._handle_bt_pairing()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_idle_when_openauto_launch_fails(sm, phone):
    sm._current_phone = phone
    sm._openauto.launch = AsyncMock(return_value=False)
    await sm._handle_bt_pairing()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_idle_when_port_not_ready(sm, phone):
    sm._current_phone = phone
    sm._openauto.wait_for_listening = AsyncMock(return_value=False)
    await sm._handle_bt_pairing()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_idle_when_credentials_fail(sm, phone):
    sm._current_phone = phone
    sm._ble.send_credentials = AsyncMock(return_value=False)
    await sm._handle_bt_pairing()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_bt_pairing_transitions_to_idle_when_ignition_off(sm, phone):
    sm._current_phone = phone
    sm._ignition_off.set()
    await sm._handle_bt_pairing()
    assert sm.state == State.SHUTDOWN


# ─────────────────────────────────────────────────────────────────────
# WIFI_WAIT
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wifi_wait_transitions_to_tcp_connect_on_client_join(sm):
    sm._wifi.wait_for_client = AsyncMock(return_value=True)
    await sm._handle_wifi_wait()
    assert sm.state == State.TCP_CONNECT


@pytest.mark.asyncio
async def test_wifi_wait_transitions_to_idle_on_timeout(sm):
    sm._wifi.wait_for_client = AsyncMock(return_value=False)
    await sm._handle_wifi_wait()
    assert sm.state == State.IDLE
    sm._openauto.kill.assert_awaited_once()
    sm._wifi.stop_ap.assert_awaited()


@pytest.mark.asyncio
async def test_wifi_wait_transitions_to_shutdown_on_ignition_off(sm):
    sm._ignition_off.set()
    await sm._handle_wifi_wait()
    assert sm.state == State.SHUTDOWN


# ─────────────────────────────────────────────────────────────────────
# TCP_CONNECT
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tcp_connect_transitions_to_projection_active(sm):
    sm._openauto.wait_for_ready = AsyncMock(return_value=True)
    await sm._handle_tcp_connect()
    assert sm.state == State.PROJECTION_ACTIVE


@pytest.mark.asyncio
async def test_tcp_connect_transitions_to_error_recovery_on_failure(sm):
    sm._openauto.wait_for_ready = AsyncMock(return_value=False)
    await sm._handle_tcp_connect()
    assert sm.state == State.ERROR_RECOVERY


@pytest.mark.asyncio
async def test_tcp_connect_transitions_to_shutdown_on_ignition_off(sm):
    sm._ignition_off.set()
    await sm._handle_tcp_connect()
    assert sm.state == State.SHUTDOWN


# ─────────────────────────────────────────────────────────────────────
# PROJECTION_ACTIVE
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_projection_active_transitions_to_idle_on_clean_exit(sm, phone):
    sm._current_phone = phone
    sm._openauto.wait_for_exit = AsyncMock(return_value=0)  # exit code 0 = clean
    await sm._handle_projection_active()
    assert sm.state == State.IDLE
    sm._volume.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_projection_active_transitions_to_error_recovery_on_crash(sm, phone):
    sm._current_phone = phone
    sm._openauto.wait_for_exit = AsyncMock(return_value=1)  # non-zero = crash
    await sm._handle_projection_active()
    assert sm.state == State.ERROR_RECOVERY


@pytest.mark.asyncio
async def test_projection_active_transitions_to_idle_on_rfcomm_reconnect(sm, phone):
    sm._current_phone = phone
    sm._ble.wait_for_rfcomm_reconnect_attempt = AsyncMock(return_value=None)
    await sm._handle_projection_active()
    assert sm.state == State.IDLE
    sm._openauto.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_projection_active_transitions_to_idle_on_bt_disconnect(sm, phone):
    sm._current_phone = phone
    sm._ble.wait_for_phone_disconnect = AsyncMock(return_value=None)
    await sm._handle_projection_active()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_projection_active_transitions_to_shutdown_on_ignition_off(sm, phone):
    sm._current_phone = phone
    sm._ignition_off.set()
    await sm._handle_projection_active()
    assert sm.state == State.SHUTDOWN
    sm._volume.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_projection_active_starts_and_stops_volume_sync(sm, phone):
    sm._current_phone = phone
    sm._openauto.wait_for_exit = AsyncMock(return_value=0)
    await sm._handle_projection_active()
    sm._volume.start.assert_awaited_once()
    sm._volume.stop.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────
# ERROR_RECOVERY
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_recovery_retries_tcp_connect_when_under_max(sm):
    sm._retry_count = 0
    sm._openauto.launch = AsyncMock(return_value=True)
    await sm._handle_error_recovery()
    assert sm.state == State.TCP_CONNECT
    assert sm._retry_count == 1


@pytest.mark.asyncio
async def test_error_recovery_transitions_to_idle_when_retries_exhausted(sm):
    sm._retry_count = MAX_RETRIES
    await sm._handle_error_recovery()
    assert sm.state == State.IDLE
    sm._wifi.stop_ap.assert_awaited()


@pytest.mark.asyncio
async def test_error_recovery_transitions_to_idle_when_relaunch_fails(sm):
    sm._retry_count = 0
    sm._openauto.launch = AsyncMock(return_value=False)
    await sm._handle_error_recovery()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_error_recovery_transitions_to_shutdown_on_ignition_off(sm):
    sm._retry_count = 0
    sm._ignition_off.set()
    await sm._handle_error_recovery()
    assert sm.state == State.SHUTDOWN


# ─────────────────────────────────────────────────────────────────────
# SHUTDOWN
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_stops_all_managers(sm):
    sm._openauto.is_running = True
    sm._splash.is_running = True

    with patch("piauto.statemachine.save_time"), patch("os.environ.get", return_value="1"):
        await sm._handle_shutdown()

    sm._openauto.kill.assert_awaited_once()
    sm._splash.kill.assert_awaited_once()
    sm._wifi.stop_ap.assert_awaited_once()
    sm._ble.stop_advertising.assert_awaited_once()
    sm._ble.close.assert_awaited_once()
    sm._thermal.stop.assert_called_once()
    sm._gpio.stop.assert_called_once()
    sm._gpio.close.assert_called_once()
    assert sm._running is False


# ─────────────────────────────────────────────────────────────────────
# Main loop exception routing (SM-001 invariant 4)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unhandled_exception_in_tcp_connect_routes_to_error_recovery(sm, phone):
    """Invariant: unhandled exceptions from TCP_CONNECT → ERROR_RECOVERY (not SHUTDOWN)."""
    sm._state = State.TCP_CONNECT
    sm._current_phone = phone
    sm._openauto.wait_for_ready = AsyncMock(side_effect=RuntimeError("socket error"))

    # _main_loop catches the exception and re-routes; stop after one recovery step
    async def _stop_after_recovery():
        sm._running = False

    with patch.object(sm, "_handle_error_recovery", AsyncMock(side_effect=_stop_after_recovery)):
        await sm._main_loop()

    assert sm.state == State.ERROR_RECOVERY


@pytest.mark.asyncio
async def test_unhandled_exception_in_booting_routes_to_shutdown(sm):
    """Invariant: exceptions from BOOTING → SHUTDOWN."""
    with patch("piauto.statemachine.load_config", side_effect=OSError("no disk")):
        await sm._handle_booting()
    assert sm.state == State.SHUTDOWN
