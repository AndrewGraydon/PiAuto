"""Bluetooth BR/EDR discovery and pairing via dbus-next.

Satisfies: FR-045 (BT speaker pairing UI), FR-046 (audio output device selection).

Provides CLI commands for the splash screen BT setup UI:
  python3 -m piauto.bt_pair scan     — BR/EDR discovery, prints DEVICE lines
  python3 -m piauto.bt_pair pair MAC — register agent, trust, pair, connect

BlueZ's D-Bus StartDiscovery requires a persistent client connection to
maintain the inquiry session. bluetoothctl's non-interactive mode drops
the connection too quickly for BR/EDR results to appear. This module
keeps the D-Bus connection alive for the full operation.
"""

from __future__ import annotations

import asyncio
import re
import sys

from piauto.log import get_logger

log = get_logger("bt_pair")

# Audio device classes (Major Device Class = 0x04, bits 12-8)
_AUDIO_MAJOR_CLASS = 0x04
_AUDIO_CLASS_MASK = 0x1F00  # bits 12-8


_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# Named timing constants used in pair()
_PAIR_WAIT_S = 1       # delay after pairing completes
_PROFILE_CHECK_S = 1   # per-iteration wait when polling for A2DP endpoints
_A2DP_SETTLE_S = 5     # wait for A2DP transport to stabilise after connect


def _is_audio_device(device_class: int) -> bool:
    """Check if a Bluetooth device class indicates an audio device."""
    major = (device_class >> 8) & 0x1F
    return major == _AUDIO_MAJOR_CLASS


async def scan(duration: float = 12.0) -> None:
    """Perform BR/EDR discovery and print found devices to stdout.

    Output format: DEVICE:<mac>:<name>:<class_hex>
    Prints SCAN_DONE when finished.
    """
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    try:
        introspect = await bus.introspect("org.bluez", "/org/bluez/hci0")
        adapter = bus.get_proxy_object("org.bluez", "/org/bluez/hci0", introspect)
        adapter_iface = adapter.get_interface("org.bluez.Adapter1")

        await adapter_iface.call_set_discovery_filter(
            {"Transport": Variant("s", "bredr")}
        )
        await adapter_iface.call_start_discovery()

        seen: set[str] = set()
        end_time = asyncio.get_running_loop().time() + duration

        while asyncio.get_running_loop().time() < end_time:
            await asyncio.sleep(1.0)

            # Introspect adapter to find child device paths
            adapter_intro = await bus.introspect("org.bluez", "/org/bluez/hci0")
            for node in adapter_intro.nodes:
                dev_path = f"/org/bluez/hci0/{node.name}"
                if dev_path in seen:
                    continue

                try:
                    dev_intro = await bus.introspect("org.bluez", dev_path)
                    ifaces = [i.name for i in dev_intro.interfaces]
                    if "org.bluez.Device1" not in ifaces:
                        continue

                    dev = bus.get_proxy_object("org.bluez", dev_path, dev_intro)
                    props = dev.get_interface("org.freedesktop.DBus.Properties")

                    name_v = await props.call_get("org.bluez.Device1", "Name")
                    cls_v = await props.call_get("org.bluez.Device1", "Class")
                    addr_v = await props.call_get("org.bluez.Device1", "Address")

                    mac = addr_v.value
                    name = name_v.value
                    dev_class = cls_v.value if cls_v.value else 0

                    seen.add(dev_path)
                    print(f"DEVICE|{mac}|{name}|{dev_class:#08x}", flush=True)
                except Exception as exc:
                    log.debug("Skipping %s: %s", dev_path, exc)
                    continue

        try:
            await adapter_iface.call_stop_discovery()
        except Exception:
            pass

    finally:
        print("SCAN_DONE", flush=True)
        bus.disconnect()


async def pair(mac: str) -> None:
    """Trust, pair, and connect to a device by MAC address.

    Registers a NoInputNoOutput agent for auto-accept pairing.
    Performs BR/EDR discovery first if the device isn't already known.

    Output: PAIR_OK:<mac>:<name> on success, PAIR_FAIL:<message> on failure.
    """
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant
    from dbus_next.service import ServiceInterface, method

    class PairingAgent(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            pass  # auto-confirm

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            pass  # auto-authorize

        @method()
        def RequestAuthorization(self, device: "o"):
            pass  # auto-authorize

        @method()
        def Cancel(self):
            pass

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    try:
        # Register pairing agent
        agent_path = "/piauto/bt_pair_agent"
        agent = PairingAgent()
        bus.export(agent_path, agent)

        bluez_intro = await bus.introspect("org.bluez", "/org/bluez")
        bluez = bus.get_proxy_object("org.bluez", "/org/bluez", bluez_intro)
        agent_mgr = bluez.get_interface("org.bluez.AgentManager1")
        await agent_mgr.call_register_agent(agent_path, "NoInputNoOutput")
        await agent_mgr.call_request_default_agent(agent_path)

        # Get adapter interface — needed for remove_device and discovery
        adapter_intro = await bus.introspect("org.bluez", "/org/bluez/hci0")
        adapter = bus.get_proxy_object(
            "org.bluez", "/org/bluez/hci0", adapter_intro
        )
        adapter_iface = adapter.get_interface("org.bluez.Adapter1")

        dev_path = "/org/bluez/hci0/dev_" + mac.replace(":", "_")

        # If BlueZ already has this device (even as paired), remove it first.
        # This handles the case where the speaker has forgotten its pairing
        # (factory reset, battery drain) but BlueZ still holds a stale entry
        # with Paired=True — which would cause call_pair() to be skipped and
        # call_connect() to fail with an authentication error.
        # Only the single selected device is removed; all other pairings are untouched.
        try:
            dev_intro = await bus.introspect("org.bluez", dev_path)
            if any(i.name == "org.bluez.Device1" for i in dev_intro.interfaces):
                dev_obj = bus.get_proxy_object("org.bluez", dev_path, dev_intro)
                try:
                    await dev_obj.get_interface("org.bluez.Device1").call_disconnect()
                except Exception:
                    pass
                await adapter_iface.call_remove_device(dev_path)
                log.info("Removed stale BlueZ entry for %s — performing clean re-pair", mac)
                await asyncio.sleep(1)
        except Exception:
            pass  # device not previously known — nothing to remove

        # Discover the device (must be in pairing mode)
        await adapter_iface.call_set_discovery_filter(
            {"Transport": Variant("s", "bredr")}
        )
        await adapter_iface.call_start_discovery()

        found = False
        for _ in range(15):
            await asyncio.sleep(1)
            try:
                dev_intro = await bus.introspect("org.bluez", dev_path)
                ifaces = [i.name for i in dev_intro.interfaces]
                if "org.bluez.Device1" in ifaces:
                    found = True
                    break
            except Exception:
                continue

        try:
            await adapter_iface.call_stop_discovery()
        except Exception:
            pass

        if not found:
            print(f"PAIR_FAIL|Device {mac} not found", flush=True)
            return

        # Trust, pair, connect
        dev_intro = await bus.introspect("org.bluez", dev_path)
        device = bus.get_proxy_object("org.bluez", dev_path, dev_intro)
        dev_iface = device.get_interface("org.bluez.Device1")
        props = device.get_interface("org.freedesktop.DBus.Properties")

        name_v = await props.call_get("org.bluez.Device1", "Name")
        name = name_v.value

        # Trust
        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))

        # Pair — device entry was removed above so this is always a fresh pair
        try:
            await dev_iface.call_pair()
        except Exception as e:
            if "AlreadyExists" not in str(e):
                print(f"PAIR_FAIL|Pairing failed: {e}", flush=True)
                return

        await asyncio.sleep(_PAIR_WAIT_S)

        # Wait for A2DP endpoints to be registered by WirePlumber before
        # attempting connect. Without endpoints, connect fails with
        # profile-unavailable.
        adapter_intro2 = await bus.introspect("org.bluez", "/org/bluez/hci0")
        adapter2 = bus.get_proxy_object(
            "org.bluez", "/org/bluez/hci0", adapter_intro2
        )
        media_iface = adapter2.get_interface("org.bluez.Media1")
        adapter_props = adapter2.get_interface("org.freedesktop.DBus.Properties")
        for wait in range(10):
            try:
                uuids = await adapter_props.call_get(
                    "org.bluez.Media1", "SupportedUUIDs"
                )
                if uuids.value:
                    break
            except Exception:
                pass
            await asyncio.sleep(_PROFILE_CHECK_S)

        # Connect (retry on profile-unavailable — WirePlumber may still be
        # registering A2DP endpoints after a service restart).
        # Disconnect between retries to clear stale ACL connections.
        last_err = None
        for attempt in range(6):
            try:
                await dev_iface.call_connect()
                last_err = None
                break
            except Exception as e:
                last_err = e
                if "profile-unavailable" in str(e) and attempt < 3:
                    try:
                        await dev_iface.call_disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(3)
                    continue
                break

        if last_err is not None:
            try:
                await dev_iface.call_disconnect()
            except Exception:
                pass
            print(f"PAIR_FAIL|Connect failed: {last_err}", flush=True)
            return

        # Wait for A2DP profile to fully establish before releasing the
        # D-Bus session. Without this, BlueZ may drop the connection when
        # our client disconnects.
        await asyncio.sleep(_A2DP_SETTLE_S)

        connected_v = await props.call_get("org.bluez.Device1", "Connected")
        if connected_v.value:
            print(f"PAIR_OK|{mac}|{name}", flush=True)
            # Hold session open so BlueZ fully settles the A2DP transport
            await asyncio.sleep(_A2DP_SETTLE_S)
        else:
            print(f"PAIR_FAIL|Connection not established", flush=True)

    except Exception as e:
        print(f"PAIR_FAIL|{e}", flush=True)
    finally:
        bus.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m piauto.bt_pair <scan|pair MAC>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "scan":
        asyncio.run(scan())
    elif cmd == "pair" and len(sys.argv) >= 3:
        mac_arg = sys.argv[2]
        if not _MAC_RE.match(mac_arg):
            print(f"Invalid MAC address: {mac_arg}", file=sys.stderr)
            sys.exit(1)
        asyncio.run(pair(mac_arg))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
