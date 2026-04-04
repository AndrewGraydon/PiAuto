"""BLE WAA advertising and RFCOMM credential exchange.

Satisfies: FR-001 to FR-005 (BLE advertisement, handshake, pairing storage).

Protocol architecture:
  1. BLE advertisement with WAA service UUID — phone discovers the head unit.
  2. Phone pairs over Classic Bluetooth (auto-accepted by our Agent1).
  3. Phone connects to RFCOMM profile — credential exchange via protobuf messages
     with 4-byte header framing.
  4. Phone disconnects BT, joins WiFi AP, connects TCP port 5000 to OpenAuto.

The credential exchange does NOT use BLE GATT characteristics. BLE is used only
for discovery. The actual data exchange happens over a Classic BT RFCOMM socket
using the WAA RFCOMM service UUID.

See PiAuto-ICD-001 §3 for protocol details.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import threading
from pathlib import Path
from typing import NamedTuple

from piauto.config import BluetoothConfig, WifiConfig
from piauto.log import get_logger

log = get_logger("ble")

# ── WAA Protocol Constants ───────────────────────────────────────────

# BLE advertisement UUID — phone's WAA client scans for this
WAA_BLE_UUID = "9b3f6c10-a4d2-418e-a2b9-0700300de8f4"

# RFCOMM service UUID — phone connects here for credential exchange
WAA_RFCOMM_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"

# RFCOMM message types (WAA protocol)
# Wire format: [uint16_be payload_length][uint16_be msg_type][protobuf payload]
MSG_WIFI_START_REQUEST = 1    # HU → Phone: AA endpoint (ip, port) — sent first by headunit
MSG_WIFI_INFO_REQUEST = 2     # Phone → HU: request WiFi credentials
MSG_WIFI_INFO_RESPONSE = 3    # HU → Phone: WiFi credentials (ssid, key, bssid, security)
MSG_WIFI_VERSION_REQUEST = 4  # (unused in standard exchange)
MSG_WIFI_VERSION_RESPONSE = 5 # (unused in standard exchange)
MSG_WIFI_CONNECT_STATUS = 6   # Phone → HU: WiFi connection result
MSG_WIFI_START_RESPONSE = 7   # Phone → HU: acknowledgement

RFCOMM_CHANNEL = 8
HEADER_SIZE = 4  # 2 bytes length + 2 bytes msg_type

# D-Bus constants
BLUEZ_SERVICE = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
PROFILE_MANAGER_IFACE = "org.bluez.ProfileManager1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

AGENT_PATH = "/piauto/agent"
ADV_PATH = "/piauto/advertisement0"
PROFILE_PATH = "/piauto/waa_profile"
HFP_HF_PROFILE_PATH = "/piauto/hfp_hf_profile"

# HFP Hands-Free profile UUID (Pi = HF client, phone = AG server)
HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"

# Pairing storage
PAIRING_DIR = Path("/data/bt")
MAX_PAIRING_RECORDS = 8

# AA projection port (fixed by AA protocol)
AA_PORT = 5000  # OpenAuto/Crankshaft wireless TCP listen port

# WiFi security mode enum (from WifiInfoResponse.proto)
# OPEN=1, WEP_64=2, WEP_128=3, WPA_PERSONAL=4, WPA2_PERSONAL=8
SECURITY_WPA2 = 8


def _run_hfp_slc(fd: int) -> None:
    """Minimal HFP HF Service Level Connection (daemon thread).

    Completes the AT command handshake with the phone's HFP AG so Android
    recognises the Pi as a connected car hands-free device.  Android Auto
    then immediately initiates the WAA RFCOMM session — matching OEM head
    unit reconnect speed — instead of waiting for BLE background scanning.

    The connection is kept open for as long as the phone maintains it.
    """
    sock = socket.fromfd(fd, socket.AF_BLUETOOTH, socket.SOCK_STREAM, 3)
    os.close(fd)  # fromfd() dups internally; close the original
    try:
        sock.settimeout(5.0)
        buf = b""

        def exchange(cmd: bytes) -> None:
            nonlocal buf
            sock.sendall(cmd + b"\r")
            while b"\r\nOK\r\n" not in buf and b"\r\nERROR\r\n" not in buf:
                chunk = sock.recv(512)
                if not chunk:
                    raise ConnectionError("HFP socket closed")
                buf += chunk
            for marker in (b"\r\nOK\r\n", b"\r\nERROR\r\n"):
                idx = buf.find(marker)
                if idx != -1:
                    buf = buf[idx + len(marker):]
                    break

        exchange(b"AT+BRSF=0")        # announce HF features (none)
        exchange(b"AT+CIND=?")        # query indicator descriptions
        exchange(b"AT+CIND?")         # query indicator values
        exchange(b"AT+CMER=3,0,0,1")  # enable indicator reporting → SLC established

        # SLC complete — keep open, draining unsolicited events until disconnect
        sock.settimeout(None)
        while sock.recv(512):
            pass
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass


class PhoneInfo(NamedTuple):
    """Information about a detected phone."""
    mac: str
    name: str


# ── Protobuf Hand-Encoding ──────────────────────────────────────────
# Hand-encoded to avoid a protobuf dependency for a handful of messages.
# Wire format follows standard protobuf encoding rules.


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _encode_string(field_num: int, value: str) -> bytes:
    """Encode a protobuf string field (wire type 2)."""
    tag = (field_num << 3) | 2
    encoded = value.encode("utf-8")
    return struct.pack("B", tag) + _encode_varint(len(encoded)) + encoded


def _encode_bytes(field_num: int, value: bytes) -> bytes:
    """Encode a protobuf bytes field (wire type 2)."""
    tag = (field_num << 3) | 2
    return struct.pack("B", tag) + _encode_varint(len(value)) + value


def _encode_varint_field(field_num: int, value: int) -> bytes:
    """Encode a protobuf varint field (wire type 0)."""
    tag = (field_num << 3) | 0
    return struct.pack("B", tag) + _encode_varint(value)


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint, returning (value, new_offset)."""
    value = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, offset
        shift += 7
    raise ValueError("Truncated varint")


# ── RFCOMM Message Framing ──────────────────────────────────────────


def _build_message(msg_type: int, payload: bytes) -> bytes:
    """Build a framed RFCOMM message: [uint16_be length][uint16_be type][payload]."""
    return struct.pack(">HH", len(payload), msg_type) + payload


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    """Receive exactly *count* bytes from a socket."""
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError("RFCOMM connection closed")
        buf += chunk
    return buf


def _recv_message(sock: socket.socket) -> tuple[int, bytes]:
    """Read one framed message. Returns (msg_type, payload)."""
    header = _recv_exact(sock, HEADER_SIZE)
    length, msg_type = struct.unpack(">HH", header)
    payload = _recv_exact(sock, length) if length > 0 else b""
    return msg_type, payload


def _msg_name(msg_type: int) -> str:
    """Human-readable name for an RFCOMM message type."""
    names = {
        MSG_WIFI_START_REQUEST: "WifiStartRequest",
        MSG_WIFI_INFO_REQUEST: "WifiInfoRequest",
        MSG_WIFI_INFO_RESPONSE: "WifiInfoResponse",
        MSG_WIFI_VERSION_REQUEST: "WifiVersionRequest",
        MSG_WIFI_VERSION_RESPONSE: "WifiVersionResponse",
        MSG_WIFI_CONNECT_STATUS: "WifiConnectStatus",
        MSG_WIFI_START_RESPONSE: "WifiStartResponse",
    }
    return names.get(msg_type, f"Unknown({msg_type})")


# ── WiFi Info Helpers ────────────────────────────────────────────────


def _get_interface_mac(interface: str = "wlan0") -> str:
    """Read MAC address of a network interface as a colon-separated string."""
    try:
        return Path(f"/sys/class/net/{interface}/address").read_text().strip().upper()
    except (FileNotFoundError, ValueError):
        log.warning("Cannot read MAC for %s — using zeros", interface)
        return "00:00:00:00:00:00"


def _build_wifi_info_response(ssid: str, password: str, bssid_str: str) -> bytes:
    """Build WifiInfoResponse protobuf payload.

    Fields (from WifiInfoResponse.proto):
      field 1: ssid (string)
      field 2: key/password (string)
      field 3: bssid (string, e.g. "AA:BB:CC:DD:EE:FF")
      field 4: security_mode (varint, 8 = WPA2_PERSONAL)
      field 5: access_point_type (varint, 1 = DYNAMIC)
    """
    return (
        _encode_string(1, ssid)
        + _encode_string(2, password)
        + _encode_string(3, bssid_str)
        + _encode_varint_field(4, SECURITY_WPA2)
        + _encode_varint_field(5, 1)
    )


def _build_wifi_start_request(ip_address: str, port: int) -> bytes:
    """Build WifiStartRequest protobuf payload (sent first by headunit).

    Fields (from WifiStartRequest.proto):
      field 1: ip_address (string)
      field 2: port (int32)
    """
    return (
        _encode_string(1, ip_address)
        + _encode_varint_field(2, port)
    )


# ── Pairing Storage ─────────────────────────────────────────────────


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
        for rec in records:
            if rec["mac"] == mac:
                rec["name"] = name
                rec["last_connected"] = timestamp
                break
        else:
            records.append({"mac": mac, "name": name, "last_connected": timestamp})
        records.sort(key=lambda r: r["last_connected"], reverse=True)
        records = records[: self._max]
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(json.dumps(records, indent=2))
        log.info("Pairing saved: %s (%s), total=%d", mac, name, len(records))

    def get_last_connected(self) -> str | None:
        """Return MAC of the most recently connected phone, or None."""
        records = self.load()
        return records[0]["mac"] if records else None


# ── BLE Manager ──────────────────────────────────────────────────────


class BleManager:
    """Manages BLE advertising, RFCOMM credential exchange, and phone detection.

    Architecture:
    - BLE advertisement with WAA UUID for phone discovery
    - BlueZ Profile1 on RFCOMM channel 8 for credential exchange
    - Auto-accept pairing agent (NoInputNoOutput)

    On non-Pi machines or without BlueZ, operates in mock mode.
    """

    def __init__(
        self,
        bt_config: BluetoothConfig,
        wifi_config: WifiConfig,
        ap_ip: str = "192.168.1.1",
        ap_interface: str = "wlan0",
    ) -> None:
        self._bt_config = bt_config
        self._wifi_config = wifi_config
        self._ap_ip = ap_ip
        self._ap_interface = ap_interface
        self._bus = None
        self._advertising = False
        self._pairing_store = PairingStore(max_records=bt_config.max_paired)
        self._rfcomm_fd: int | None = None
        self._rfcomm_device_path: str | None = None
        self._connection_queue: asyncio.Queue | None = None
        self._adv_registered = False
        self._profile_registered = False
        self._hfp_hf_registered = False

    async def setup(self) -> bool:
        """Connect to system D-Bus, register agent and RFCOMM profile."""
        try:
            from dbus_next.aio import MessageBus
            from dbus_next import BusType
            self._bus = await MessageBus(
                bus_type=BusType.SYSTEM,
                negotiate_unix_fd=True,
            ).connect()
        except Exception as exc:
            log.warning("Cannot connect to system D-Bus: %s — BLE in mock mode", exc)
            return False

        try:
            introspection = await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez")
            if introspection is None:
                log.warning("BlueZ service not found on D-Bus")
                return False
        except Exception as exc:
            log.warning("BlueZ not available: %s", exc)
            return False

        # Register pairing agent
        try:
            await self._register_agent()
        except Exception as exc:
            log.warning("Failed to register pairing agent: %s", exc)

        # Register RFCOMM profile for credential exchange
        try:
            await self._register_rfcomm_profile()
        except Exception as exc:
            log.warning("Failed to register RFCOMM profile: %s", exc)

        log.info("BLE manager ready (ap_ip=%s, ap_iface=%s)", self._ap_ip, self._ap_interface)
        return True

    # ── D-Bus registrations ──────────────────────────────────────

    async def _register_agent(self) -> None:
        """Register a NoInputNoOutput agent with BlueZ for auto-accept pairing."""
        from dbus_next.service import ServiceInterface, method

        class PairingAgent(ServiceInterface):
            """BlueZ Agent1 that auto-accepts all pairing requests."""

            def __init__(self) -> None:
                super().__init__(AGENT_IFACE)

            @method()
            def Release(self):  # noqa: N802
                log.debug("Agent released")

            @method()
            def RequestPinCode(self, device: "o") -> "s":  # noqa: N802
                log.info("PIN requested for %s — returning '0000'", device)
                return "0000"

            @method()
            def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802
                log.info("Display PIN %s for %s", pincode, device)

            @method()
            def RequestPasskey(self, device: "o") -> "u":  # noqa: N802
                log.info("Passkey requested for %s — returning 0", device)
                return 0

            @method()
            def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802
                log.info("Display passkey %06d for %s", passkey, device)

            @method()
            def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802
                log.info("Auto-confirming passkey %06d for %s", passkey, device)

            @method()
            def RequestAuthorization(self, device: "o"):  # noqa: N802
                log.info("Auto-authorizing %s", device)

            @method()
            def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802
                log.info("Auto-authorizing service %s for %s", uuid, device)

            @method()
            def Cancel(self):  # noqa: N802
                log.debug("Agent pairing cancelled")

        self._agent = PairingAgent()
        self._bus.export(AGENT_PATH, self._agent)

        agent_manager = self._bus.get_proxy_object(
            BLUEZ_SERVICE, "/org/bluez",
            await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez"),
        )
        mgr = agent_manager.get_interface(AGENT_MANAGER_IFACE)
        await mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
        await mgr.call_request_default_agent(AGENT_PATH)
        log.info("BlueZ pairing agent registered (auto-accept)")

    async def _register_rfcomm_profile(self) -> None:
        """Register the WAA RFCOMM profile with BlueZ ProfileManager1.

        BlueZ will create an SDP record with RFCOMM protocol descriptor
        (auto-assigned channel) and route incoming connections to our
        Profile1.NewConnection method, which receives the connected socket fd.
        """
        from dbus_next.service import ServiceInterface, method
        from dbus_next import Variant

        self._connection_queue = asyncio.Queue()
        queue = self._connection_queue

        class WaaProfile(ServiceInterface):
            """BlueZ Profile1 for WAA RFCOMM credential exchange."""

            def __init__(self) -> None:
                super().__init__("org.bluez.Profile1")

            @method()
            def Release(self):  # noqa: N802
                log.debug("RFCOMM profile released")

            @method()
            def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}"):  # noqa: N802
                duped = os.dup(fd)
                log.info("RFCOMM NewConnection: device=%s fd=%d (duped=%d)", device, fd, duped)
                queue.put_nowait((device, duped))

            @method()
            def RequestDisconnection(self, device: "o"):  # noqa: N802
                log.info("RFCOMM RequestDisconnection: %s", device)

        self._waa_profile = WaaProfile()
        self._bus.export(PROFILE_PATH, self._waa_profile)

        profile_mgr = self._bus.get_proxy_object(
            BLUEZ_SERVICE, "/org/bluez",
            await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez"),
        )
        mgr = profile_mgr.get_interface(PROFILE_MANAGER_IFACE)
        await mgr.call_register_profile(
            PROFILE_PATH,
            WAA_RFCOMM_UUID,
            {
                "Channel": Variant("q", 0),
                "AutoConnect": Variant("b", False),
                "Role": Variant("s", "server"),
                "Name": Variant("s", "Wireless Android Auto"),
            },
        )
        self._profile_registered = True
        log.info("RFCOMM profile registered (UUID: %s, channel: auto)", WAA_RFCOMM_UUID)

    async def _register_hfp_hf_profile(self) -> None:
        """Register an HFP Hands-Free (HF) client profile with BlueZ.

        When Device1.Connect() is called on the phone, BlueZ connects this
        HFP HF profile to the phone's HFP AG (UUID 0x111f).  Android detects
        a car hands-free device and immediately triggers Android Auto, which
        then initiates the WAA RFCOMM — matching OEM head unit behaviour.
        """
        from dbus_next.service import ServiceInterface, method
        from dbus_next import Variant

        class HfpHfProfile(ServiceInterface):
            def __init__(self) -> None:
                super().__init__("org.bluez.Profile1")

            @method()
            def Release(self):  # noqa: N802
                pass

            @method()
            def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}"):  # noqa: N802
                duped = os.dup(fd)
                log.info("HFP HF connected: %s — establishing SLC", device)
                threading.Thread(
                    target=_run_hfp_slc, args=(duped,), daemon=True
                ).start()

            @method()
            def RequestDisconnection(self, device: "o"):  # noqa: N802
                log.debug("HFP HF disconnected: %s", device)

        self._hfp_hf_profile = HfpHfProfile()
        self._bus.export(HFP_HF_PROFILE_PATH, self._hfp_hf_profile)

        profile_mgr = self._bus.get_proxy_object(
            BLUEZ_SERVICE, "/org/bluez",
            await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez"),
        )
        mgr = profile_mgr.get_interface(PROFILE_MANAGER_IFACE)
        await mgr.call_register_profile(
            HFP_HF_PROFILE_PATH,
            HFP_HF_UUID,
            {
                "Channel": Variant("q", 0),
                "AutoConnect": Variant("b", True),
                "Role": Variant("s", "client"),
                "Name": Variant("s", "Hands-Free"),
                "Version": Variant("q", 0x0108),  # HFP 1.8
                "Features": Variant("q", 0x0000),  # minimal — no HF features
            },
        )
        log.info("HFP HF profile registered (UUID: %s)", HFP_HF_UUID)
        self._hfp_hf_registered = True

    async def _register_ble_advertisement(self) -> None:
        """Register a BLE advertisement with the WAA service UUID."""
        from dbus_next.service import ServiceInterface, method, dbus_property
        from dbus_next import PropertyAccess

        device_name = self._bt_config.device_name

        class WaaAdvertisement(ServiceInterface):
            """BLE advertisement exposing the WAA service UUID for phone discovery."""

            def __init__(self) -> None:
                super().__init__("org.bluez.LEAdvertisement1")

            @method()
            def Release(self):  # noqa: N802
                log.debug("BLE advertisement released")

            @dbus_property(access=PropertyAccess.READ)
            def Type(self) -> "s":  # noqa: N802
                return "peripheral"

            @dbus_property(access=PropertyAccess.READ)
            def ServiceUUIDs(self) -> "as":  # noqa: N802
                return [WAA_BLE_UUID]

            @dbus_property(access=PropertyAccess.READ)
            def LocalName(self) -> "s":  # noqa: N802
                return device_name

            @dbus_property(access=PropertyAccess.READ)
            def Includes(self) -> "as":  # noqa: N802
                return ["tx-power"]

        if not self._adv_registered:
            self._advertisement = WaaAdvertisement()
            self._bus.export(ADV_PATH, self._advertisement)

        adapter_proxy = self._bus.get_proxy_object(
            BLUEZ_SERVICE, "/org/bluez/hci0",
            await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
        )
        adv_mgr = adapter_proxy.get_interface(LE_ADV_MANAGER_IFACE)
        await adv_mgr.call_register_advertisement(ADV_PATH, {})
        self._adv_registered = True
        log.info("BLE advertisement registered (UUID: %s)", WAA_BLE_UUID)

    # ── Public API (matches state machine interface) ─────────────

    async def start_advertising(self) -> None:
        """Start BLE advertising with the WAA service UUID.

        Also sets the adapter to powered, discoverable, and pairable so the
        phone can discover and pair via both BLE and Classic BT.
        """
        if not self._bus:
            log.info("BLE advertising (mock mode)")
            self._advertising = True
            return

        try:
            # Power on adapter and set discoverable
            adapter_proxy = self._bus.get_proxy_object(
                BLUEZ_SERVICE, "/org/bluez/hci0",
                await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
            )
            props = adapter_proxy.get_interface(PROPERTIES_IFACE)
            await props.call_set(ADAPTER_IFACE, "Powered", _variant(True))
            await props.call_set(ADAPTER_IFACE, "DiscoverableTimeout", _variant(0))
            await props.call_set(ADAPTER_IFACE, "Discoverable", _variant(True))
            await props.call_set(ADAPTER_IFACE, "Pairable", _variant(True))
            await props.call_set(ADAPTER_IFACE, "Alias", _variant(self._bt_config.device_name))

            # Register BLE advertisement
            await self._register_ble_advertisement()

            self._advertising = True
            log.info("BLE advertising started (BLE UUID: %s, RFCOMM UUID: %s)",
                     WAA_BLE_UUID, WAA_RFCOMM_UUID)

        except Exception as exc:
            log.error("Failed to start BLE advertising: %s", exc)
            self._advertising = False

    async def stop_advertising(self) -> None:
        """Stop BLE advertising and set adapter non-discoverable."""
        if self._bus and self._adv_registered:
            try:
                adapter_proxy = self._bus.get_proxy_object(
                    BLUEZ_SERVICE, "/org/bluez/hci0",
                    await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
                )
                adv_mgr = adapter_proxy.get_interface(LE_ADV_MANAGER_IFACE)
                await adv_mgr.call_unregister_advertisement(ADV_PATH)
            except Exception as exc:
                log.warning("Error unregistering BLE advertisement: %s", exc)
            # Unexport the D-Bus object so it can be re-exported on next start
            try:
                self._bus.unexport(ADV_PATH)
            except Exception:
                pass
            self._adv_registered = False

        if self._bus:
            try:
                adapter_proxy = self._bus.get_proxy_object(
                    BLUEZ_SERVICE, "/org/bluez/hci0",
                    await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez/hci0"),
                )
                props = adapter_proxy.get_interface(PROPERTIES_IFACE)
                await props.call_set(ADAPTER_IFACE, "Discoverable", _variant(False))
            except Exception as exc:
                log.warning("Error setting discoverable=False: %s", exc)

        self._advertising = False
        log.info("BLE advertising stopped")

    async def wait_for_phone(self, timeout: float = 0) -> PhoneInfo | None:
        """Wait for a phone to connect via the RFCOMM WAA profile.

        Blocks until a phone establishes an RFCOMM connection to our Profile1.
        Returns PhoneInfo on connection, None on timeout/cancel.
        """
        if not self._bus:
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

        if not self._connection_queue:
            log.error("RFCOMM profile not registered — cannot wait for phone")
            return None

        log.info("Waiting for RFCOMM connection from phone...")

        try:
            if timeout > 0:
                device_path, fd = await asyncio.wait_for(
                    self._connection_queue.get(), timeout=timeout
                )
            else:
                device_path, fd = await self._connection_queue.get()

            # Extract MAC and name from D-Bus device path
            mac, name = _extract_device_info(device_path)
            if self._bus:
                name = await _get_device_name(self._bus, device_path, fallback=mac)

            self._rfcomm_fd = fd
            self._rfcomm_device_path = device_path
            phone = PhoneInfo(mac=mac, name=name)
            log.info("Phone connected via RFCOMM: %s (%s)", phone.name, phone.mac)
            return phone

        except TimeoutError:
            log.debug("Phone detection timed out")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("Error waiting for phone: %s", exc)

        return None

    async def wait_for_rfcomm_during_projection(self) -> None:
        """Block until a new RFCOMM connection arrives during an active session.

        During PROJECTION_ACTIVE, a new RFCOMM connection means the phone has
        ended the AA session and is attempting to restart it (the phone sends
        these periodically after disconnecting AA).  The fd is closed immediately
        — IDLE will wait for the next one via wait_for_phone().
        """
        if not self._connection_queue:
            await asyncio.get_running_loop().create_future()  # block forever
            return
        device_path, fd = await self._connection_queue.get()
        log.info(
            "New RFCOMM connection during projection — phone reconnecting: %s",
            device_path,
        )
        try:
            os.close(fd)
        except OSError:
            pass

    async def send_credentials(self, phone: PhoneInfo) -> bool:
        """Exchange WiFi credentials with the phone over RFCOMM.

        Message exchange:
        1. Read WifiStartRequest from phone (msg type 1)
        2. Optionally read WifiInfoRequest (msg type 2)
        3. Send WifiInfoResponse with AP credentials (msg type 3)
        4. Send WifiStartResponse with AA endpoint (msg type 4)
        5. Optionally read WifiConnectStatus (msg type 7)

        Returns True on successful exchange.
        """
        if not self._bus:
            log.info("Credentials sent (mock mode)")
            return True

        if self._rfcomm_fd is None:
            log.error("No RFCOMM connection — cannot send credentials")
            return False

        fd = self._rfcomm_fd
        log.info(
            "Starting RFCOMM credential exchange with %s: ssid=%s, ip=%s, port=%d",
            phone.mac, self._wifi_config.ssid, self._ap_ip, AA_PORT,
        )

        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, self._rfcomm_exchange, fd)
            return success
        except Exception as exc:
            log.error("RFCOMM credential exchange failed: %s", exc)
            return False
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._rfcomm_fd = None

    def save_pairing(self, phone: PhoneInfo, timestamp: float) -> None:
        """Persist a pairing record."""
        self._pairing_store.save_pairing(phone.mac, phone.name, timestamp)

    async def trust_device(self, mac: str) -> None:
        """Set Trusted=True on a BlueZ device so it auto-reconnects on future boots.

        BlueZ auto-connects trusted devices when they come into range, matching
        the behaviour of bt_pair.py for BT speakers. Without this the Pi must
        wait for Android's BLE background scan (several minutes) each boot.
        """
        if not self._bus:
            return
        dev_path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
        try:
            from dbus_next import Variant
            dev_intro = await self._bus.introspect("org.bluez", dev_path)
            dev = self._bus.get_proxy_object("org.bluez", dev_path, dev_intro)
            props = dev.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
            log.info("Device %s marked Trusted in BlueZ", mac)
        except Exception as exc:
            log.debug("Failed to trust %s: %s", mac, exc)

    def get_last_connected_mac(self) -> str | None:
        """Get the MAC of the most recently connected phone."""
        return self._pairing_store.get_last_connected()

    async def try_reconnect_phone(self, mac: str) -> None:
        """Page a previously paired phone and connect HFP to trigger Android Auto.

        OEM head units connect in seconds by:
          1. Calling Device1.Connect() — establishes ACL + A2DP
          2. Calling Device1.ConnectProfile(HFP_AG_UUID) — connects HFP HF→AG
        Android detects HFP as a 'car hands-free device', immediately triggers
        Android Auto, which then initiates the WAA RFCOMM back to the Pi.
        Non-fatal if the phone is out of range or rejects the connection.
        """
        if not self._bus:
            return
        dev_path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
        try:
            dev_intro = await self._bus.introspect("org.bluez", dev_path)
            if not any(i.name == "org.bluez.Device1" for i in dev_intro.interfaces):
                log.debug("Phone %s not in BlueZ cache — skipping auto-reconnect", mac)
                return
            dev = self._bus.get_proxy_object("org.bluez", dev_path, dev_intro)
            # Ensure trusted so BlueZ persists auto-connect across boots
            try:
                from dbus_next import Variant as _Variant
                props = dev.get_interface("org.freedesktop.DBus.Properties")
                await props.call_set("org.bluez.Device1", "Trusted", _Variant("b", True))
            except Exception:
                pass
            dev_iface = dev.get_interface("org.bluez.Device1")
            log.info("Auto-reconnect: paging last known phone %s", mac)
            # Step 1: establish ACL + A2DP
            await asyncio.wait_for(dev_iface.call_connect(), timeout=15.0)
            # Step 2: register HFP HF profile NOW — while the device is already
            # connected.  BlueZ AutoConnect=True fires immediately when a profile
            # is registered for a device that is already connected, which is
            # exactly what we need.  Registering before connect (in setup) does
            # NOT trigger the same path — BlueZ only auto-connects on the
            # profile-registration event, not on the device-connect event.
            if not self._hfp_hf_registered:
                try:
                    await self._register_hfp_hf_profile()
                except Exception as exc:
                    log.debug("Auto-reconnect: HFP registration failed: %s", exc)
        except asyncio.TimeoutError:
            log.debug("Auto-reconnect to %s: timed out (phone not in range)", mac)
        except Exception as exc:
            log.debug("Auto-reconnect to %s: %s", mac, exc)

    async def close(self) -> None:
        """Clean up RFCOMM fd and disconnect from D-Bus."""
        if self._rfcomm_fd is not None:
            try:
                os.close(self._rfcomm_fd)
            except OSError:
                pass
            self._rfcomm_fd = None

        if self._bus:
            if self._profile_registered:
                try:
                    profile_mgr = self._bus.get_proxy_object(
                        BLUEZ_SERVICE, "/org/bluez",
                        await self._bus.introspect(BLUEZ_SERVICE, "/org/bluez"),
                    )
                    mgr = profile_mgr.get_interface(PROFILE_MANAGER_IFACE)
                    await mgr.call_unregister_profile(PROFILE_PATH)
                except Exception as exc:
                    log.debug("Error unregistering RFCOMM profile: %s", exc)
                self._profile_registered = False

            self._bus.disconnect()
            self._bus = None
        log.info("BLE manager closed")

    # ── RFCOMM exchange (runs in executor thread) ────────────────

    def _rfcomm_exchange(self, fd: int) -> bool:
        """Synchronous RFCOMM protobuf message exchange.

        Creates a socket from the fd with a 15 s recv timeout, then performs
        the message exchange. Runs in a thread via run_in_executor.
        """
        # Wrap fd in a socket for timeout support
        sock = socket.fromfd(fd, socket.AF_BLUETOOTH, socket.SOCK_STREAM, 3)
        sock.settimeout(15.0)
        try:
            return self._do_rfcomm_exchange(sock)
        finally:
            sock.close()  # closes the dup'd fd from fromfd()

    def _do_rfcomm_exchange(self, sock: socket.socket) -> bool:
        """Handle the WAA protobuf message exchange on a connected RFCOMM socket.

        Sequence (from WirelessAndroidAutoDongle / aa-proxy-rs):
          1. HU → Phone: WifiStartRequest (ip, port)
          2. Phone → HU: WifiInfoRequest
          3. HU → Phone: WifiInfoResponse (ssid, key, bssid, security, ap_type)
          4. Phone → HU: WifiStartResponse
          5. Phone → HU: WifiConnectStatus
        """
        try:
            # Step 1: Send WifiStartRequest (HU sends first)
            start_req = _build_wifi_start_request(self._ap_ip, AA_PORT)
            msg = _build_message(MSG_WIFI_START_REQUEST, start_req)
            sock.sendall(msg)
            log.info("RFCOMM sent: WifiStartRequest (%d bytes) ip=%s port=%d",
                     len(msg), self._ap_ip, AA_PORT)

            # Step 2: Read WifiInfoRequest from phone
            msg_type, payload = _recv_message(sock)
            log.info("RFCOMM recv: %s (type=%d, %d bytes)",
                     _msg_name(msg_type), msg_type, len(payload))

            # Step 3: Send WifiInfoResponse with AP credentials
            bssid = _get_interface_mac(self._ap_interface)
            wifi_info = _build_wifi_info_response(
                ssid=self._wifi_config.ssid,
                password=self._wifi_config.password,
                bssid_str=bssid,
            )
            msg = _build_message(MSG_WIFI_INFO_RESPONSE, wifi_info)
            sock.sendall(msg)
            log.info("RFCOMM sent: WifiInfoResponse (%d bytes) ssid=%s bssid=%s",
                     len(msg), self._wifi_config.ssid, bssid)

            # Step 4: Read WifiStartResponse from phone
            msg_type2, payload2 = _recv_message(sock)
            log.info("RFCOMM recv: %s (type=%d, %d bytes)",
                     _msg_name(msg_type2), msg_type2, len(payload2))

            # Credential exchange is complete at this point — phone has our
            # WiFi info and will connect asynchronously. WifiConnectStatus
            # (step 5) arrives after the phone joins the AP, which may take
            # several seconds. Read it with a short timeout; if it doesn't
            # arrive, that's fine — WIFI_WAIT will detect the phone joining.
            try:
                old_timeout = sock.gettimeout()
                sock.settimeout(5.0)
                msg_type3, payload3 = _recv_message(sock)
                log.info("RFCOMM recv: %s (type=%d, %d bytes)",
                         _msg_name(msg_type3), msg_type3, len(payload3))
                if msg_type3 == MSG_WIFI_CONNECT_STATUS and len(payload3) >= 2:
                    if payload3[1] != 0:
                        log.warning("Phone reported WiFi connect issue (status byte: 0x%02x)",
                                    payload3[1])
                sock.settimeout(old_timeout)
            except (socket.timeout, ConnectionError, OSError):
                log.debug("WifiConnectStatus not received (phone connecting to AP asynchronously)")

            log.info("RFCOMM credential exchange completed successfully")
            return True

        except Exception as exc:
            log.error("RFCOMM exchange error: %s", exc)
            return False


# ── Helpers ──────────────────────────────────────────────────────────


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


def _extract_device_info(device_path: str) -> tuple[str, str]:
    """Extract MAC from a D-Bus device path like /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF."""
    mac = "unknown"
    try:
        dev_part = device_path.rsplit("/", 1)[-1]
        if dev_part.startswith("dev_"):
            mac = dev_part[4:].replace("_", ":")
    except Exception:
        pass
    return mac, mac  # name defaults to MAC, overridden by _get_device_name


async def _get_device_name(bus, device_path: str, fallback: str = "unknown") -> str:
    """Query BlueZ for the device's friendly name."""
    try:
        dev_proxy = bus.get_proxy_object(
            BLUEZ_SERVICE, device_path,
            await bus.introspect(BLUEZ_SERVICE, device_path),
        )
        dev_props = dev_proxy.get_interface(PROPERTIES_IFACE)
        name_var = await dev_props.call_get("org.bluez.Device1", "Name")
        return name_var.value if hasattr(name_var, "value") else str(name_var)
    except Exception:
        return fallback
