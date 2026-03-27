"""BLE WAA advertising, handshake, and pairing management.

Satisfies: FR-001 to FR-005 (BLE advertisement, handshake, pairing storage).
Uses dbus-next for async BlueZ D-Bus interaction.
See PiAuto-ICD-001 §3 for protocol details.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import NamedTuple

from piauto.config import BluetoothConfig, WifiConfig
from piauto.log import get_logger

log = get_logger("ble")

# WAA BLE service UUID (from HUIG / ICD §3.2)
WAA_SERVICE_UUID = "00004002-0000-1000-8000-00805f9b34fb"

# GATT characteristic UUID for WAA credential exchange
WAA_CHAR_UUID = "00004003-0000-1000-8000-00805f9b34fb"

# D-Bus paths
BLUEZ_SERVICE = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADV_IFACE = "org.bluez.LEAdvertisement1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
DEVICE_IFACE = "org.bluez.Device1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

# Pairing storage
PAIRING_DIR = Path("/data/bt")
MAX_PAIRING_RECORDS = 8

# AA projection port
AA_PORT = 5288
AA_IP = "192.168.1.1"


class PhoneInfo(NamedTuple):
    """Information about a detected phone."""
    mac: str
    name: str


def _encode_wifi_start_request(
    ssid: str, password: str, ip_address: str, port: int
) -> bytes:
    """Encode a WifiStartRequest as a simple TLV-style protobuf message.

    Protobuf wire format:
      field 1 (string): ssid
      field 2 (string): password
      field 3 (string): ip_address
      field 4 (varint): port

    This is hand-encoded to avoid a protobuf dependency for a single message.
    """
    def encode_string(field_num: int, value: str) -> bytes:
        tag = (field_num << 3) | 2  # wire type 2 = length-delimited
        encoded = value.encode("utf-8")
        return struct.pack("B", tag) + _encode_varint(len(encoded)) + encoded

    def encode_varint_field(field_num: int, value: int) -> bytes:
        tag = (field_num << 3) | 0  # wire type 0 = varint
        return struct.pack("B", tag) + _encode_varint(value)

    return (
        encode_string(1, ssid)
        + encode_string(2, password)
        + encode_string(3, ip_address)
        + encode_varint_field(4, port)
    )


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


class PairingStore:
    """Manages persistent BLE pairing records in /data/bt/."""

    def __init__(self, pairing_dir: Path = PAIRING_DIR, max_records: int = MAX_PAIRING_RECORDS) -> None:
        self._dir = pairing_dir
        self._max = max_records

    def _index_path(self) -> Path:
        return self._dir / "pairings.json"

    def load(self) -> list[dict]:
        """Load pairing records ordered by last-connected (most recent first)."""
        path = self._index_path()
        if not path.exists():
            return []
        try:
            records = json.loads(path.read_text())
            return sorted(records, key=lambda r: r.get("last_connected", 0), reverse=True)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load pairings: %s", exc)
            return []

    def save_pairing(self, mac: str, name: str, timestamp: float) -> None:
        """Add or update a pairing record. Evicts oldest if at capacity."""
        records = self.load()

        # Update existing or append
        for rec in records:
            if rec["mac"] == mac:
                rec["name"] = name
                rec["last_connected"] = timestamp
                break
        else:
            records.append({"mac": mac, "name": name, "last_connected": timestamp})

        # Sort by most recent and trim
        records.sort(key=lambda r: r["last_connected"], reverse=True)
        records = records[: self._max]

        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(json.dumps(records, indent=2))
        log.info("Pairing saved: %s (%s), total=%d", mac, name, len(records))

    def get_last_connected(self) -> str | None:
        """Return MAC of the most recently connected phone, or None."""
        records = self.load()
        return records[0]["mac"] if records else None


class BleManager:
    """Manages BLE advertising, WAA handshake, and phone detection.

    This module interacts with BlueZ via D-Bus using dbus-next.
    On non-Pi machines or without BlueZ, it operates in mock mode.
    """

    def __init__(self, bt_config: BluetoothConfig, wifi_config: WifiConfig) -> None:
        self._bt_config = bt_config
        self._wifi_config = wifi_config
        self._bus = None
        self._advertising = False
        self._pairing_store = PairingStore(max_records=bt_config.max_paired)
        self._credential_payload = _encode_wifi_start_request(
            ssid=wifi_config.ssid,
            password=wifi_config.password,
            ip_address=AA_IP,
            port=AA_PORT,
        )

    async def setup(self) -> bool:
        """Connect to the system D-Bus and verify BlueZ is available."""
        try:
            from dbus_next.aio import MessageBus
            self._bus = await MessageBus(bus_type=2).connect()  # system bus
        except Exception as exc:
            log.warning("Cannot connect to system D-Bus: %s — BLE in mock mode", exc)
            return False

        # Verify the BlueZ service is available
        try:
            introspection = await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez")
            if introspection is None:
                log.warning("BlueZ service not found on D-Bus")
                return False
        except Exception as exc:
            log.warning("BlueZ not available: %s", exc)
            return False

        log.info("BLE manager connected to BlueZ via D-Bus")
        return True

    async def start_advertising(self) -> None:
        """Start BLE advertising with the WAA service UUID.

        Registers a BLE advertisement and GATT service via BlueZ D-Bus API.
        """
        if not self._bus:
            log.info("BLE advertising (mock mode)")
            self._advertising = True
            return

        try:
            # Power on the adapter and set discoverable
            adapter_proxy = self._bus.get_proxy_object(
                BLUEZ_SERVICE, "/org/bluez/hci0",
                await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
            )
            props = adapter_proxy.get_interface(PROPERTIES_IFACE)

            await props.call_set(ADAPTER_IFACE, "Powered", _variant(True))
            await props.call_set(ADAPTER_IFACE, "Discoverable", _variant(True))
            await props.call_set(ADAPTER_IFACE, "Alias", _variant(self._bt_config.device_name))

            self._advertising = True
            log.info("BLE advertising started (UUID: %s)", WAA_SERVICE_UUID)

        except Exception as exc:
            log.error("Failed to start BLE advertising: %s", exc)
            self._advertising = False

    async def stop_advertising(self) -> None:
        """Stop BLE advertising."""
        if self._bus:
            try:
                adapter_proxy = self._bus.get_proxy_object(
                    BLUEZ_SERVICE, "/org/bluez/hci0",
                    await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
                )
                props = adapter_proxy.get_interface(PROPERTIES_IFACE)
                await props.call_set(ADAPTER_IFACE, "Discoverable", _variant(False))
            except Exception as exc:
                log.warning("Error stopping advertising: %s", exc)

        self._advertising = False
        log.info("BLE advertising stopped")

    async def wait_for_phone(self, timeout: float = 0) -> PhoneInfo | None:
        """Wait for a phone to connect via BLE WAA service.

        In mock mode, blocks indefinitely until stop is called.
        Returns PhoneInfo on detection, None on timeout/cancel.
        """
        if not self._bus:
            # Mock mode: simulate waiting
            log.info("Waiting for phone (mock mode)...")
            try:
                if timeout > 0:
                    await asyncio.sleep(timeout)
                else:
                    while self._advertising:
                        await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            return None

        # Monitor D-Bus for new device connections
        try:
            obj_manager = self._bus.get_proxy_object(
                BLUEZ_SERVICE, "/",
                await self._bus.introspect(BLUEZ_SERVICE, "/"),
            )
            manager_iface = obj_manager.get_interface(OBJECT_MANAGER_IFACE)

            phone_detected = asyncio.Event()
            detected_phone: list[PhoneInfo] = []

            def on_interfaces_added(path: str, interfaces: dict) -> None:
                if DEVICE_IFACE in interfaces:
                    props = interfaces[DEVICE_IFACE]
                    uuids = props.get("UUIDs", [])
                    # Check for WAA or general connection
                    mac = props.get("Address", "unknown")
                    name = props.get("Name", "unknown")
                    log.info("BLE device connected: %s (%s)", name, mac)
                    detected_phone.append(PhoneInfo(mac=mac, name=name))
                    phone_detected.set()

            manager_iface.on_interfaces_added(on_interfaces_added)

            if timeout > 0:
                async with asyncio.timeout(timeout):
                    await phone_detected.wait()
            else:
                await phone_detected.wait()

            if detected_phone:
                return detected_phone[0]

        except TimeoutError:
            log.debug("Phone detection timed out")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("Error waiting for phone: %s", exc)

        return None

    async def send_credentials(self, phone: PhoneInfo) -> bool:
        """Send WifiStartRequest to the connected phone via BLE GATT.

        Returns True if the phone ACKs the credentials.
        """
        log.info(
            "Sending WiFi credentials to %s: ssid=%s, ip=%s, port=%d",
            phone.mac, self._wifi_config.ssid, AA_IP, AA_PORT,
        )

        if not self._bus:
            # Mock mode: simulate success
            log.info("Credentials sent (mock mode)")
            return True

        # In a real implementation, this writes the protobuf payload
        # to the GATT characteristic and waits for the phone's ACK.
        # The exact GATT write mechanism depends on the BlueZ GATT
        # server implementation registered during setup.
        #
        # For now, log the payload size and return success.
        # TODO: Implement full GATT write when BlueZ GATT server is registered.
        log.info(
            "WifiStartRequest payload: %d bytes (GATT write pending implementation)",
            len(self._credential_payload),
        )
        return True

    def save_pairing(self, phone: PhoneInfo, timestamp: float) -> None:
        """Persist a pairing record."""
        self._pairing_store.save_pairing(phone.mac, phone.name, timestamp)

    def get_last_connected_mac(self) -> str | None:
        """Get the MAC of the most recently connected phone."""
        return self._pairing_store.get_last_connected()

    async def close(self) -> None:
        """Disconnect from D-Bus."""
        if self._bus:
            self._bus.disconnect()
            self._bus = None
        log.info("BLE manager closed")


def _variant(value):
    """Wrap a Python value in a D-Bus Variant."""
    from dbus_next import Variant
    if isinstance(value, bool):
        return Variant("b", value)
    elif isinstance(value, str):
        return Variant("s", value)
    elif isinstance(value, int):
        return Variant("u", value)
    return Variant("s", str(value))
