"""System time initialization for boards without a battery-backed RTC.

Satisfies: FR-042 (set clock from /data/clock), FR-043 (TLS cert independence).
See PiAuto-IG-001 §11.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from piauto.log import get_logger

log = get_logger("clock")

DEFAULT_CLOCK_FILE = Path("/data/clock")


def restore_time(clock_file: Path = DEFAULT_CLOCK_FILE) -> None:
    """Set the system clock from the saved timestamp, if available.

    This ensures monotonically increasing time across boots even without
    NTP or an RTC. Called during BOOTING state entry.
    """
    if not clock_file.exists():
        log.info("No saved clock file at %s — skipping time restore", clock_file)
        return

    try:
        epoch = int(clock_file.read_text().strip())
    except (ValueError, OSError) as exc:
        log.warning("Failed to read clock file %s: %s", clock_file, exc)
        return

    current = int(time.time())
    if current >= epoch:
        log.info(
            "System time (%d) already ahead of saved time (%d) — no adjustment",
            current,
            epoch,
        )
        return

    try:
        subprocess.run(
            ["date", "-s", f"@{epoch}"],
            check=True,
            capture_output=True,
        )
        log.info("System clock set to saved time: epoch %d", epoch)
    except subprocess.CalledProcessError as exc:
        log.warning("Failed to set system clock: %s", exc.stderr.decode().strip())
    except FileNotFoundError:
        log.warning("'date' command not found — cannot set system clock")


def save_time(clock_file: Path = DEFAULT_CLOCK_FILE) -> None:
    """Persist the current system time to disk. Called during SHUTDOWN."""
    try:
        clock_file.parent.mkdir(parents=True, exist_ok=True)
        clock_file.write_text(str(int(time.time())))
        log.info("System time saved to %s", clock_file)
    except OSError as exc:
        log.warning("Failed to save clock to %s: %s", clock_file, exc)
