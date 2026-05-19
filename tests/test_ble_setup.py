"""Tests for BleManager.setup() failure modes.

Verifies that D-Bus and BlueZ registration failures cause setup() to return
False (fail-fast) rather than continuing silently into mock mode.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from piauto.ble import BleManager
from piauto.config import BluetoothConfig, WifiConfig


def _make_manager() -> BleManager:
    return BleManager(
        bt_config=BluetoothConfig(),
        wifi_config=WifiConfig(password="testpass1"),
        ap_ip="192.168.4.1",
        ap_interface="wlan0",
    )


# ── D-Bus connection failures ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_returns_false_on_dbus_connect_failure():
    mgr = _make_manager()
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(side_effect=OSError("no D-Bus"))
        result = await mgr.setup()
    assert result is False


@pytest.mark.asyncio
async def test_setup_returns_false_when_bluez_not_found():
    mgr = _make_manager()
    mock_bus = AsyncMock()
    mock_bus.introspect = AsyncMock(return_value=None)
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        result = await mgr.setup()
    assert result is False


@pytest.mark.asyncio
async def test_setup_returns_false_when_bluez_introspect_raises():
    mgr = _make_manager()
    mock_bus = AsyncMock()
    mock_bus.introspect = AsyncMock(side_effect=Exception("BlueZ gone"))
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        result = await mgr.setup()
    assert result is False


# ── Registration failures (the new fail-fast behaviour) ───────────────


@pytest.mark.asyncio
async def test_setup_returns_false_on_agent_registration_failure():
    """Agent registration failure must not be swallowed — setup() returns False."""
    mgr = _make_manager()
    mock_bus = AsyncMock()
    mock_bus.introspect = AsyncMock(return_value=object())  # non-None = BlueZ present
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        with patch.object(mgr, "_register_agent", AsyncMock(side_effect=Exception("agent fail"))):
            result = await mgr.setup()
    assert result is False


@pytest.mark.asyncio
async def test_setup_returns_false_on_rfcomm_registration_failure():
    """RFCOMM registration failure must not be swallowed — setup() returns False."""
    mgr = _make_manager()
    mock_bus = AsyncMock()
    mock_bus.introspect = AsyncMock(return_value=object())
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        with patch.object(mgr, "_register_agent", AsyncMock()):
            with patch.object(mgr, "_register_rfcomm_profile", AsyncMock(side_effect=Exception("rfcomm fail"))):
                result = await mgr.setup()
    assert result is False


@pytest.mark.asyncio
async def test_setup_returns_true_when_all_registrations_succeed():
    """Happy path: D-Bus connects, BlueZ present, both registrations succeed."""
    mgr = _make_manager()
    mock_bus = AsyncMock()
    mock_bus.introspect = AsyncMock(return_value=object())
    with patch("dbus_next.aio.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        with patch.object(mgr, "_register_agent", AsyncMock()):
            with patch.object(mgr, "_register_rfcomm_profile", AsyncMock()):
                result = await mgr.setup()
    assert result is True
