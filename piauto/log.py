"""Logging setup for PiAuto — journald on Pi, stderr on dev machines.

Satisfies: NR-007 (journald ring buffer logging).
"""

import logging
import sys


def _has_journald() -> bool:
    try:
        from systemd.journal import JournalHandler  # noqa: F401
        return True
    except ImportError:
        return False


def setup_logging() -> None:
    """Configure the root logger for the piauto namespace.

    On a Pi with python3-systemd installed, logs go to journald.
    On a dev machine, logs go to stderr.
    """
    root = logging.getLogger("piauto")
    if root.handlers:
        return  # already configured

    root.setLevel(logging.DEBUG)

    if _has_journald():
        from systemd.journal import JournalHandler
        handler = JournalHandler(SYSLOG_IDENTIFIER="piauto")
    else:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "%(asctime)s %(name)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)

    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the piauto namespace."""
    return logging.getLogger(f"piauto.{name}")
