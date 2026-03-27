"""GPIO interface for ignition sense and fan PWM control.

Satisfies: FR-032 (ignition sense GPIO 17), FR-035 (fan PWM GPIO 4).
Pin assignments per PiAuto-HW-001 and PiAuto-ICD-001 §11–12.

Uses libgpiod v2 Python bindings. Gracefully degrades on non-Pi machines.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from piauto.log import get_logger

log = get_logger("gpio")

# Pin assignments (BCM numbering)
IGNITION_PIN = 17
FAN_PIN = 4
FAN_PWM_FREQ_HZ = 25_000

_gpiod_available = False
try:
    import gpiod  # type: ignore[import-untyped]
    from gpiod.line import Bias, Direction, Drive, Edge, Value  # type: ignore[import-untyped]
    _gpiod_available = True
except ImportError:
    log.warning("gpiod not available — GPIO functions will be no-ops")


class GpioManager:
    """Manages ignition sense input and fan PWM output."""

    def __init__(self) -> None:
        self._chip: gpiod.Chip | None = None  # type: ignore[name-defined]
        self._ignition_request = None
        self._fan_request = None
        self._running = False

    async def setup(self) -> None:
        """Open GPIO chip and configure lines."""
        if not _gpiod_available:
            log.warning("GPIO setup skipped — not on Pi hardware")
            return

        try:
            self._chip = gpiod.Chip("/dev/gpiochip4")
        except (FileNotFoundError, PermissionError) as exc:
            log.warning("Cannot open GPIO chip: %s — running in mock mode", exc)
            return

        # Ignition sense: input with pull-up, watch falling edge
        self._ignition_request = self._chip.request_lines(
            config={
                IGNITION_PIN: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                    edge_detection=Edge.FALLING,
                    debounce_period=None,  # software debounce handled below
                ),
            },
            consumer="piauto-ignition",
        )

        # Fan PWM: output, initially off
        self._fan_request = self._chip.request_lines(
            config={
                FAN_PIN: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    drive=Drive.PUSH_PULL,
                    output_value=Value.INACTIVE,
                ),
            },
            consumer="piauto-fan",
        )

        log.info("GPIO initialized: ignition=GPIO%d, fan=GPIO%d", IGNITION_PIN, FAN_PIN)

    async def monitor_ignition(
        self,
        callback: Callable[[], None],
        debounce_ms: int = 500,
    ) -> None:
        """Poll for ignition-off events and call `callback` when confirmed.

        Debounces the signal: GPIO 17 must remain LOW for `debounce_ms`
        before the callback fires. Runs until `stop()` is called.
        """
        if not self._ignition_request:
            log.info("Ignition monitoring disabled (no GPIO)")
            # In mock mode, just block forever
            self._running = True
            while self._running:
                await asyncio.sleep(1)
            return

        self._running = True
        debounce_s = debounce_ms / 1000.0

        while self._running:
            # Wait for edge event with a timeout so we can check self._running
            ready = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._ignition_request.wait_edge_events(timeout=1.0),
            )
            if not ready or not self._running:
                continue

            self._ignition_request.read_edge_events()

            # Debounce: verify the pin stays LOW
            await asyncio.sleep(debounce_s)
            if not self._running:
                return

            value = self._ignition_request.get_value(IGNITION_PIN)
            if value == Value.INACTIVE:  # LOW = ignition off
                log.info("Ignition OFF confirmed (debounced %d ms)", debounce_ms)
                callback()
                return

    def set_fan_duty(self, percent: int) -> None:
        """Set fan PWM duty cycle (0 = off, 50, 100).

        Note: True hardware PWM requires /sys/class/pwm or pigpio.
        This implementation uses simple on/off control as a baseline:
        0% = OFF, 1-99% = ON (for software PWM, a periodic task would be needed),
        100% = ON.

        TODO: Implement proper hardware PWM via /sys/class/pwm/pwmchip0
        when running on Pi hardware.
        """
        if not self._fan_request:
            log.debug("Fan duty set to %d%% (no-op, no GPIO)", percent)
            return

        percent = max(0, min(100, percent))

        if percent == 0:
            self._fan_request.set_value(FAN_PIN, Value.INACTIVE)
        else:
            self._fan_request.set_value(FAN_PIN, Value.ACTIVE)

        log.debug("Fan duty set to %d%%", percent)

    def stop(self) -> None:
        """Stop monitoring and release GPIO resources."""
        self._running = False

        if self._fan_request:
            self._fan_request.set_value(FAN_PIN, Value.INACTIVE)

    def close(self) -> None:
        """Release all GPIO resources."""
        self.stop()
        if self._ignition_request:
            self._ignition_request.release()
            self._ignition_request = None
        if self._fan_request:
            self._fan_request.release()
            self._fan_request = None
        if self._chip:
            self._chip.close()
            self._chip = None
        log.info("GPIO resources released")
