"""CPU temperature monitoring and fan profile control.

Satisfies: FR-035 (PWM fan control with thermal profile).
Profile: OFF < 50 C, 50% at 50-65 C, 100% above 65 C.
Hysteresis prevents rapid cycling (default 3 C).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from piauto.config import ThermalConfig
from piauto.gpio import GpioManager
from piauto.log import get_logger

log = get_logger("thermal")

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


class ThermalMonitor:
    """Reads CPU temperature and adjusts fan speed."""

    def __init__(self, config: ThermalConfig, gpio: GpioManager) -> None:
        self._config = config
        self._gpio = gpio
        self._running = False
        self._fan_on = False

    def _read_temp(self) -> float | None:
        """Read CPU temperature in degrees Celsius."""
        try:
            millidegrees = int(THERMAL_ZONE.read_text().strip())
            return millidegrees / 1000.0
        except (FileNotFoundError, ValueError, OSError):
            return None

    def _compute_duty(self, temp_c: float) -> int:
        """Determine fan duty cycle based on temperature and hysteresis."""
        low = self._config.fan_low_temp
        high = self._config.fan_high_temp
        hyst = self._config.hysteresis

        if self._fan_on:
            # Fan is on — apply hysteresis before turning off
            if temp_c < low - hyst:
                return 0
            elif temp_c >= high:
                return 100
            else:
                return 50
        else:
            # Fan is off — need to exceed threshold to turn on
            if temp_c >= high:
                return 100
            elif temp_c >= low:
                return 50
            else:
                return 0

    async def run(self) -> None:
        """Poll temperature and adjust fan. Runs until stop() is called."""
        self._running = True
        interval = self._config.poll_interval

        temp = self._read_temp()
        if temp is None:
            log.warning(
                "Cannot read %s — thermal monitoring disabled", THERMAL_ZONE
            )
            while self._running:
                await asyncio.sleep(1)
            return

        log.info("Thermal monitoring started (poll every %d s)", interval)

        _read_failures = 0
        while self._running:
            temp = self._read_temp()
            if temp is not None:
                if _read_failures > 0:
                    log.info("Thermal sensor recovered after %d failed read(s)", _read_failures)
                _read_failures = 0
                duty = self._compute_duty(temp)
                self._fan_on = duty > 0
                self._gpio.set_fan_duty(duty)
                log.debug("CPU %.1f C → fan %d%%", temp, duty)
            else:
                _read_failures += 1
                # Log on first failure and every 12 subsequent failures (~1 min at 5s poll)
                if _read_failures == 1 or _read_failures % 12 == 0:
                    log.warning(
                        "Cannot read thermal sensor (%s) — fan held at last duty "
                        "(failure count: %d)",
                        THERMAL_ZONE, _read_failures,
                    )

            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        self._gpio.set_fan_duty(0)
        log.info("Thermal monitoring stopped")
