# PiAuto — Claude Code Instructions

## Project Overview
Wireless Android Auto head unit for Raspberry Pi 4B. Python 3.11+ orchestrator managing BLE discovery, WiFi AP, and OpenAuto (C++ AA protocol handler).

## Specification Documents
All specs are in `docs/`. Code must trace back to requirement IDs:
- **SRS** (PiAuto-SRS-001): FR-001–047, PR-001–005, NR-001–007
- **State Machine** (PiAuto-SM-001): Authoritative for all state transitions
- **ICD** (PiAuto-ICD-001): Interface details (BLE, WiFi, GPIO, AA protocol)
- **Implementation Guide** (PiAuto-IG-001): Config schema, templates, code patterns

## Code Style
- Python 3.11+ with type hints
- asyncio for all I/O-bound operations
- Dataclasses for structured data
- Module-level `log = get_logger("module_name")` for logging (use `piauto.log`)
- No X11/Wayland — display is Qt EGLFS (direct KMS/DRM)

## Architecture Rules
- State machine (`statemachine.py`) is the sole orchestrator — no module initiates actions independently
- Display ownership: exactly one process (splash OR OpenAuto) owns DRM master at any time
- OpenAuto is an external C++ binary managed via subprocess — we do NOT implement AA protocol in Python
- Hardware-dependent modules must gracefully degrade on non-Pi machines (try/except on import, log warning, provide no-op fallback)

## Key Dependencies
- `dbus-next` — async BlueZ D-Bus access
- `PyYAML` — config loading
- `gpiod` — GPIO (optional, Pi-only)
- `PyQt5` — splash screen only (optional, Pi-only)

## Config
- Default path: `/data/piauto.yaml` (override via `PIAUTO_CONFIG_PATH` env var)
- Missing/invalid config → log warning, use built-in defaults, continue booting

## Testing
- `pytest` + `pytest-asyncio` for async tests
- Hardware modules should be testable via mocks on dev machines
