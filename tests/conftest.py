"""Shared fixtures for PiAuto tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from piauto.ble import PhoneInfo
from piauto.config import PiAutoConfig
from piauto.statemachine import StateMachine


async def _never(*args, **kwargs):
    """Block until cancelled — use as side_effect for tasks that should never fire."""
    await asyncio.Event().wait()


@pytest.fixture
def phone() -> PhoneInfo:
    return PhoneInfo(mac="AA:BB:CC:DD:EE:FF", name="TestPhone")


@pytest.fixture
def sm() -> StateMachine:
    """StateMachine with all hardware managers replaced by AsyncMocks."""
    machine = StateMachine()
    machine._config = PiAutoConfig()

    gpio = MagicMock()
    gpio.setup = AsyncMock()
    gpio.monitor_ignition = AsyncMock(side_effect=_never)
    gpio.stop = MagicMock()
    gpio.close = MagicMock()
    machine._gpio = gpio

    ble = MagicMock()
    ble.wait_for_phone = AsyncMock(side_effect=_never)
    ble.wait_for_rfcomm_reconnect_attempt = AsyncMock(side_effect=_never)
    ble.wait_for_phone_disconnect = AsyncMock(side_effect=_never)
    ble.watch_bluez_restart = AsyncMock(side_effect=_never)
    ble.start_advertising = AsyncMock()
    ble.stop_advertising = AsyncMock()
    ble.send_credentials = AsyncMock(return_value=True)
    ble.save_pairing = MagicMock()
    ble.trust_device = AsyncMock()
    ble.close = AsyncMock()
    ble.get_last_connected_mac = MagicMock(return_value=None)
    ble.try_reconnect_phone = AsyncMock(side_effect=_never)
    machine._ble = ble

    wifi = MagicMock()
    wifi.stop_ap = AsyncMock()
    wifi.start_ap = AsyncMock(return_value=True)
    wifi.wait_for_client = AsyncMock(return_value=True)
    wifi.wait_for_client_leave_ap = AsyncMock(side_effect=_never)
    wifi.ap_ip = "192.168.4.1"
    wifi.ap_interface = "wlan0"
    machine._wifi = wifi

    oa = MagicMock()
    oa.is_running = False
    oa.kill = AsyncMock()
    oa.launch = AsyncMock(return_value=True)
    oa.wait_for_listening = AsyncMock(return_value=True)
    oa.wait_for_ready = AsyncMock(return_value=True)
    oa.wait_for_exit = AsyncMock(side_effect=_never)
    oa.wait_for_projection_stopped = AsyncMock(side_effect=_never)
    oa.wait_for_tcp_session_end = AsyncMock(side_effect=_never)
    machine._openauto = oa

    splash = MagicMock()
    splash.is_running = False
    splash.launch = AsyncMock()
    splash.kill = AsyncMock()
    splash.kill_and_wait_drm_release = AsyncMock()
    splash.read_stdout_line = AsyncMock(side_effect=_never)
    machine._splash = splash

    thermal = MagicMock()
    thermal.run = AsyncMock(side_effect=_never)
    thermal.stop = MagicMock()
    machine._thermal = thermal

    volume = MagicMock()
    volume.start = AsyncMock()
    volume.stop = AsyncMock()
    machine._volume = volume

    machine._ignition_off = asyncio.Event()
    machine._ignition_task = None
    machine._thermal_task = None

    return machine
