"""AVRCP volume sync — maps BT AVRCP volume to PipeWire sink.

Satisfies: FR-020 (audio output control via PipeWire).
See PiAuto-IG-001 §13.

The AA protocol sends raw PCM audio with no volume control messages.
The phone's volume buttons change its BT AVRCP volume (a side-effect
of the WAA BT connection). This module polls that AVRCP value and
maps it to the PipeWire default audio sink via wpctl.

Uses dbus_next async API so D-Bus calls yield the event loop rather
than blocking it (replaces the prior synchronous python-dbus approach).
"""

from __future__ import annotations

import asyncio

from piauto.log import get_logger

log = get_logger("volume")

# Poll interval in seconds — fast enough to feel responsive
_POLL_INTERVAL = 0.3


class VolumeSyncManager:
    """Polls BlueZ MediaTransport1.Volume and syncs to PipeWire."""

    def __init__(self) -> None:
        self._poll_task: asyncio.Task | None = None
        self._wpctl_proc: asyncio.subprocess.Process | None = None
        self._last_volumes: dict[str, int] = {}

    async def start(self) -> None:
        """Start the background volume sync task."""
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("AVRCP volume sync started")

    async def stop(self) -> None:
        """Stop the background volume sync task."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            log.info("AVRCP volume sync stopped")

    async def _poll_loop(self) -> None:
        """Poll AVRCP volumes and sync changes to PipeWire."""
        from dbus_next.aio import MessageBus
        from dbus_next import BusType
        from dbus_next.errors import DBusError

        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as exc:
            log.warning("Volume sync: cannot connect to D-Bus: %s", exc)
            return

        try:
            while True:
                try:
                    transports = await self._get_transports(bus)
                    for path, vol in transports.items():
                        if vol >= 0 and self._last_volumes.get(path) != vol:
                            linear = vol / 127.0
                            log.info(
                                "AVRCP %s %d/127 → PipeWire %.2f", path, vol, linear
                            )
                            await self._set_pipewire_volume(linear)
                            self._last_volumes[path] = vol
                except asyncio.CancelledError:
                    raise
                except DBusError:
                    # BlueZ may be temporarily unavailable during reconnects
                    pass
                except Exception as exc:
                    log.debug("Volume sync error: %s", exc)

                await asyncio.sleep(_POLL_INTERVAL)
        finally:
            bus.disconnect()

    @staticmethod
    async def _get_transports(bus) -> dict[str, int]:
        """Get all MediaTransport1 paths and their Volume values."""
        introspect = await bus.introspect("org.bluez", "/")
        obj = bus.get_proxy_object("org.bluez", "/", introspect)
        manager = obj.get_interface("org.freedesktop.DBus.ObjectManager")
        managed = await manager.call_get_managed_objects()

        result: dict[str, int] = {}
        for path, interfaces in managed.items():
            if "org.bluez.MediaTransport1" in interfaces:
                vol = interfaces["org.bluez.MediaTransport1"].get("Volume")
                result[str(path)] = int(vol.value) if vol is not None else -1
        return result

    async def _set_pipewire_volume(self, linear: float) -> None:
        """Set PipeWire default sink volume via wpctl."""
        # Skip if a previous wpctl invocation is still running
        if self._wpctl_proc is not None and self._wpctl_proc.returncode is None:
            return
        try:
            self._wpctl_proc = await asyncio.create_subprocess_exec(
                "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{linear:.3f}",
                env={"XDG_RUNTIME_DIR": "/run/user/1000"},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self._wpctl_proc.wait()
        except FileNotFoundError:
            log.debug("wpctl not found")
