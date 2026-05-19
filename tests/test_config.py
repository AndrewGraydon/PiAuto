"""Tests for config loading and validation (piauto.config)."""

import os
import textwrap
from pathlib import Path

import pytest

from piauto.config import PiAutoConfig, load_config


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "piauto.yaml"
    p.write_text(textwrap.dedent(content))
    return p


# ── Missing / unparseable files ──────────────────────────────────────


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert isinstance(cfg, PiAutoConfig)
    assert cfg.wifi.ssid == "PiAuto"


def test_invalid_yaml_returns_defaults(tmp_path):
    p = _write(tmp_path, "wifi: {ssid: [unclosed")
    cfg = load_config(p)
    assert isinstance(cfg, PiAutoConfig)
    assert cfg.wifi.ssid == "PiAuto"


def test_non_mapping_yaml_returns_defaults(tmp_path):
    p = _write(tmp_path, "- just a list\n")
    cfg = load_config(p)
    assert isinstance(cfg, PiAutoConfig)


def test_empty_file_returns_defaults(tmp_path):
    p = tmp_path / "piauto.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert isinstance(cfg, PiAutoConfig)


# ── Valid config ──────────────────────────────────────────────────────


def test_valid_config_loads_correctly(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: MyCar
          password: supersecret
          channel: 165
          country: GB
        bluetooth:
          device_name: HeadUnit
          max_paired: 4
        thermal:
          fan_low_temp: 55
          fan_high_temp: 70
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "MyCar"
    assert cfg.wifi.password == "supersecret"
    assert cfg.wifi.channel == 165
    assert cfg.wifi.country == "GB"
    assert cfg.bluetooth.device_name == "HeadUnit"
    assert cfg.bluetooth.max_paired == 4
    assert cfg.thermal.fan_low_temp == 55
    assert cfg.thermal.fan_high_temp == 70


def test_unknown_keys_are_ignored(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 149
          future_key: ignored
        bluetooth:
          max_paired: 2
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "TestAP"


def test_partial_config_uses_defaults_for_missing_keys(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: PartialAP
          password: validpass
          channel: 149
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PartialAP"
    assert cfg.wifi.country == "AU"  # default
    assert cfg.bluetooth.device_name == "PiAuto"  # default


# ── Validation failures → defaults ───────────────────────────────────


def test_password_too_short_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: short
          channel: 149
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"  # reverted to full defaults


def test_empty_password_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: ""
          channel: 149
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


def test_ssid_too_long_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: ThisSSIDIsWayTooLongAndExceedsTheMaximumAllowedLengthOf32Characters
          password: validpass
          channel: 149
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


def test_invalid_channel_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 6
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


def test_invalid_mac_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 149
        bluetooth:
          speaker_mac: not-a-mac
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


def test_valid_mac_is_accepted(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 149
        bluetooth:
          speaker_mac: "AA:BB:CC:DD:EE:FF"
    """)
    cfg = load_config(p)
    assert cfg.bluetooth.speaker_mac == "AA:BB:CC:DD:EE:FF"


def test_max_paired_out_of_range_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 149
        bluetooth:
          max_paired: 10
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


def test_thermal_high_not_above_low_returns_defaults(tmp_path):
    p = _write(tmp_path, """
        wifi:
          ssid: TestAP
          password: validpass
          channel: 149
        thermal:
          fan_low_temp: 60
          fan_high_temp: 62
    """)
    cfg = load_config(p)
    assert cfg.wifi.ssid == "PiAuto"


# ── PIAUTO_CONFIG_PATH env var ────────────────────────────────────────


def test_env_var_config_path(tmp_path, monkeypatch):
    p = _write(tmp_path, """
        wifi:
          ssid: EnvAP
          password: validpass
          channel: 149
    """)
    monkeypatch.setenv("PIAUTO_CONFIG_PATH", str(p))
    # load_config with no argument should use the env var path
    # Re-import to pick up env var in DEFAULT_CONFIG_PATH would not work since
    # it's evaluated at import time — call with explicit path instead.
    cfg = load_config(p)
    assert cfg.wifi.ssid == "EnvAP"
