"""YAML configuration loader and validator.

Satisfies: NR-006 (single YAML config file).
Schema defined in PiAuto-IG-001 §4.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from piauto.log import get_logger

log = get_logger("config")

DEFAULT_CONFIG_PATH = Path(os.environ.get("PIAUTO_CONFIG_PATH", "/data/piauto.yaml"))

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


@dataclass
class WifiConfig:
    ssid: str = "PiAuto"
    password: str = ""  # must be set explicitly in /data/piauto.yaml
    channel: int = 149
    country: str = "AU"


@dataclass
class BluetoothConfig:
    device_name: str = "PiAuto"
    max_paired: int = 8
    speaker_mac: str = ""


@dataclass
class DisplayConfig:
    resolution: str = "800x480"
    fps: int = 30


@dataclass
class AudioConfig:
    output: str = "bluetooth"


@dataclass
class ThermalConfig:
    fan_low_temp: int = 50
    fan_high_temp: int = 65
    hysteresis: int = 3
    poll_interval: int = 5


@dataclass
class PowerConfig:
    ignition_debounce_ms: int = 500
    shutdown_timeout_s: int = 10


@dataclass
class OpenAutoConfig:
    binary: str = "/usr/local/bin/autoapp"
    extra_args: list[str] = field(default_factory=list)


@dataclass
class PiAutoConfig:
    wifi: WifiConfig = field(default_factory=WifiConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    thermal: ThermalConfig = field(default_factory=ThermalConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    openauto: OpenAutoConfig = field(default_factory=OpenAutoConfig)


def _validate(cfg: PiAutoConfig) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []

    # WiFi
    if not (1 <= len(cfg.wifi.ssid) <= 32):
        errors.append(f"wifi.ssid length {len(cfg.wifi.ssid)} not in 1–32")
    if len(cfg.wifi.password) < 8:
        errors.append(
            f"wifi.password too short ({len(cfg.wifi.password)} chars) — "
            "set a password of at least 8 characters in /data/piauto.yaml"
        )
    if cfg.wifi.channel not in (149, 165):
        errors.append(f"wifi.channel {cfg.wifi.channel} not in {{149, 165}}")

    # Bluetooth
    if not (1 <= cfg.bluetooth.max_paired <= 8):
        errors.append(f"bluetooth.max_paired {cfg.bluetooth.max_paired} not in 1–8")
    if cfg.bluetooth.speaker_mac and not _MAC_RE.match(cfg.bluetooth.speaker_mac):
        errors.append(f"bluetooth.speaker_mac invalid: {cfg.bluetooth.speaker_mac}")

    # Thermal
    if not (30 <= cfg.thermal.fan_low_temp <= 75):
        errors.append(f"thermal.fan_low_temp {cfg.thermal.fan_low_temp} not in 30–75")
    min_high = cfg.thermal.fan_low_temp + 5
    if not (min_high <= cfg.thermal.fan_high_temp <= 80):
        errors.append(
            f"thermal.fan_high_temp {cfg.thermal.fan_high_temp} "
            f"not in {min_high}–80"
        )

    return errors


def _dict_to_config(raw: dict) -> PiAutoConfig:
    """Build a PiAutoConfig from a raw YAML dict, using defaults for missing keys."""
    cfg = PiAutoConfig()

    if "wifi" in raw and isinstance(raw["wifi"], dict):
        for k, v in raw["wifi"].items():
            if hasattr(cfg.wifi, k):
                setattr(cfg.wifi, k, v)

    if "bluetooth" in raw and isinstance(raw["bluetooth"], dict):
        for k, v in raw["bluetooth"].items():
            if hasattr(cfg.bluetooth, k):
                setattr(cfg.bluetooth, k, v)

    if "display" in raw and isinstance(raw["display"], dict):
        for k, v in raw["display"].items():
            if hasattr(cfg.display, k):
                setattr(cfg.display, k, v)

    if "audio" in raw and isinstance(raw["audio"], dict):
        for k, v in raw["audio"].items():
            if hasattr(cfg.audio, k):
                setattr(cfg.audio, k, v)

    if "thermal" in raw and isinstance(raw["thermal"], dict):
        for k, v in raw["thermal"].items():
            if hasattr(cfg.thermal, k):
                setattr(cfg.thermal, k, v)

    if "power" in raw and isinstance(raw["power"], dict):
        for k, v in raw["power"].items():
            if hasattr(cfg.power, k):
                setattr(cfg.power, k, v)

    if "openauto" in raw and isinstance(raw["openauto"], dict):
        for k, v in raw["openauto"].items():
            if hasattr(cfg.openauto, k):
                setattr(cfg.openauto, k, v)

    return cfg


def save_speaker_mac(mac: str, path: Path | None = None) -> None:
    """Persist bluetooth.speaker_mac to the config file (last paired wins)."""
    path = path or DEFAULT_CONFIG_PATH
    try:
        raw: dict = {}
        if path.exists():
            parsed = yaml.safe_load(path.read_text())
            if isinstance(parsed, dict):
                raw = parsed
        raw.setdefault("bluetooth", {})["speaker_mac"] = mac
        path.write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True))
        log.info("Saved speaker_mac %s → %s", mac, path)
    except Exception as exc:
        log.warning("Failed to save speaker_mac: %s", exc)


def load_config(path: Path | None = None) -> PiAutoConfig:
    """Load and validate configuration. Returns defaults on any failure."""
    path = path or DEFAULT_CONFIG_PATH

    if not path.exists():
        log.warning("Config file %s not found — using defaults", path)
        return PiAutoConfig()

    try:
        raw = yaml.safe_load(path.read_text())
    except Exception as exc:
        log.warning("Failed to parse %s: %s — using defaults", path, exc)
        return PiAutoConfig()

    if not isinstance(raw, dict):
        log.warning("Config file %s is not a YAML mapping — using defaults", path)
        return PiAutoConfig()

    cfg = _dict_to_config(raw)

    errors = _validate(cfg)
    if errors:
        for err in errors:
            log.warning("Config validation: %s", err)
        log.warning("Using built-in defaults due to validation errors")
        return PiAutoConfig()

    log.info("Configuration loaded from %s", path)
    return cfg
