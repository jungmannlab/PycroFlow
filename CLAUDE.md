# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PycroFlow is a Python framework for coordinating microscopy image acquisition, fluid handling (Hamilton liquid handlers), and illumination control in automated fluorescence microscopy experiments (Exchange-PAINT, MERPAINT, Z-PAINT). It targets Windows 10 with hardware serial communication. Python 3.10+.

See `ARCHITECTURE.md` for the package map, `docs/architecture.md` for detail, and `docs/adr/` for the rationale behind major decisions.

## Commands

### Install
```bash
pip install -e ".[dev]"        # dev / CI (hardware libs mocked in tests)
pip install -e ".[hardware]"   # lab Windows box (real instruments)
pip install -e ".[gui]"        # PyQt6 for the `pycroflow-gui` frontend
```
Console scripts: `pycroflow` (CLI), `pycroflow-gui` (Qt GUI).
All metadata and dependencies live in `pyproject.toml`; `setup.py` is a thin shim. There is no `requirements.txt` — its former contents are fully covered by the core `dependencies` plus the `[hardware]` / `[dev]` / `[gui]` extras.

### Run all tests
```bash
cd /Users/hgrabmayr/GitHub/PycroFlow
python -m unittest discover -v
```
The suite runs without vendor SDKs: `PycroFlow/tests/_mock_hardware.py` installs `sys.modules` mocks for pycromanager / monet / pycobolt / nidaqmx when they're not importable. Real SDKs are preferred when present.

### Run a single test file / case
```bash
python -m unittest PycroFlow.tests.test_protocols -v
python -m unittest PycroFlow.tests.test_protocols.TestProtocolBuilder.test_05 -v
```

### Coverage
`coverage` + `pytest-cov` are in the `[dev]` extra and configured under `[tool.coverage.*]` in `pyproject.toml` (scoped to the `PycroFlow` package, tests/emulators omitted), so no `--source`/path flags are needed.

Inline in the pytest run (via `pytest-cov`):
```bash
pytest --cov                          # coverage summary printed after the test run
pytest --cov --cov-report=term-missing
pytest --cov --cov-report=html        # also writes htmlcov/
```
Standalone (unittest runner, or pytest without the plugin):
```bash
coverage run -m unittest discover && coverage report
coverage run -m pytest && coverage report
coverage html                         # browsable report in htmlcov/
```
The suite is unittest-style; pytest works as an alternate runner via the top-level `conftest.py`, which installs the same hardware mocks as `tests/__init__.py`.

### Regenerate protocol regression snapshots (after an intended wire change)
```bash
PYCROFLOW_UPDATE_SNAPSHOTS=1 python -m unittest PycroFlow.tests.test_regression_protocols -v
```
Commit the updated JSON in `PycroFlow/tests/fixtures/snapshots/`.

CI runs `python -m unittest discover -v` on Windows / Python 3.10 (`.github/workflows/tests.yml`). There are no configured linters/formatters yet (`ruff`/`mypy` are in the `[dev]` extra).

### Hardware emulators (`tests/emulators/`)
Behavioral hardware fakes for tests, in three fidelity layers (vs. the import-only `MagicMock` shims in `tests/_mock_hardware.py`):
- **Serial-level** — `hamilton_serial.FakeHamiltonSerial` (+ `patch_serial()` / `make_fake_bus()`) presents a `serial.Serial` surface speaking the Hamilton PSD/MVP wire protocol, so the *real* `SerialBus` / `Pump` / `Valve` run end-to-end (covers the command encode/response decode path). `arduino_serial.FakeArduinoSerial` (+ `connect_interface()`) does the same for `ArduinoSensorInterface`.
- **HAL-level** — `hal_devices.EmulatedPump` / `EmulatedValve` / `EmulatedSpillSensor` implement the `hal/` ABCs with in-memory state + a command log.
- **Subsystem-level** — `subsystems.EmulatedFluidSystem` / `EmulatedImagingSystem` / `EmulatedIlluminationSystem` implement `AbstractSystem` with deterministic pause/resume/abort for driving `ProtocolOrchestrator`.

Tests live in `tests/test_emulators.py`.

## Architecture

### Orchestration (`orchestration/`)
`ProtocolOrchestrator` (in `orchestration/core.py`) manages `FluidHandler` / `ImagingHandler` / `IlluminationHandler` daemon threads. Cross-subsystem sync uses a signal protocol (`$type: 'signal'` / `'wait for signal'`). `orchestration/signal_registry.py` provides an `Event`-backed `SignalRegistry` (no busy-poll, hard timeout); `orchestration/threadexchange.py` provides a per-instance `ThreadExchange` (locks/events/lists/registry). Supports pause/resume/abort. Typed-entry dispatch via `functools.singledispatch`.

### Protocol system (`protocols/` + `protocol_entries.py` + `schemas/`)
`ProtocolBuilder` (`protocols/builder.py`) transforms high-level experiment descriptions into per-subsystem entry lists. Experiment types dispatch through the `EXPERIMENT_TYPES` registry (`exchange`, `merpaint`, `flushtest`, `sph-resi`). Each entry is a dict with a `$type` field; `schemas/protocol_schema.py` validates them (pydantic discriminated union, `extra='allow'`), and `protocol_entries.py` exposes the typed models + `parse_entry` / `parse_protocol`.

### Fluid automation (`fluid/` + `pyHamilton/` + `hal/`)
`LegacyArchitecture` (`fluid/legacy.py`) drives Hamilton MVP valves and PSD syringe pumps over serial. Instrument topology lives in `configs/legacy_system.yaml` and `configs/legacy_tubing.yaml`, loaded by `configs/__init__.py` and re-exported as `legacy_system_config` / `legacy_tubing_config`. `pyHamilton/` is the in-house serial driver (`SerialBus` in `communication.py`, `command.py`, `mvp.py`, `psd.py`). `hal/` defines vendor-neutral `Pump` / `Valve` / `SpillSensor` ABCs.

### Imaging (`imaging.py` + `services/mm_core.py` + `mm_lock.py`)
`ImagingSystem` wraps pycromanager for acquisition and PFS monitoring. The MM Core/Studio singletons are owned by `services/mm_core.py` (supersedes `util.PyMgrSingleton`). `mm_lock.MmCoreLock` is a filesystem mutex that prevents PycroFlow imaging and a standalone monet GUI from attaching to MM simultaneously (raises `MmLockHeld`).

### Illumination (`illumination.py`)
`IlluminationSystem` manages laser power/wavelength via **monet**, which is an external sibling repository (not vendored — see `docs/adr/004`). Tests mock it.

### Services (`services/`)
Frontend-agnostic layer both the CLI and the Qt GUI consume: `ExperimentService` (lifecycle + observer hooks), `SystemService` (manual hardware control), `mm_core` (Core ownership).

### Spill sensor (`spill_sensor_arduino.py`)
`ArduinoSensorInterface` polls an Arduino over serial for wetness/spill detection in a background thread. Port via the `PYCROFLOW_SPILL_PORT` env var.

### Frontends (`frontend_cli.py`, `gui/`)
`PycroFlowInteractive` (`cmd.Cmd`) is the `pycroflow` console entry point; lifecycle commands route through `services/`. `gui/` is the `pycroflow-gui` PyQt6 frontend (`[gui]` extra): a tabbed `PycroFlowMainWindow` (Experiment / Fluid / Imaging / Monet) sitting on the same `services/` layer. The Monet tab embeds monet's `MonetMainWindow` in-process (sharing one MM Core via `mm_core.share_with_monet()`); `gui/qt_bridge.py` marshals service observer callbacks onto the GUI thread as Qt signals. The package is import-safe without PyQt6 — Qt is imported lazily so `import PycroFlow.gui` and the test suite work without the `[gui]` extra. **Binding caveat:** the embedded Monet tab only works when monet is also on PyQt6; while monet is still on PyQt5 the tab degrades to a placeholder (PyQt5/PyQt6 widgets can't share a process).

### Logging
loguru, configured by `PycroFlow.setup_logging(clean_old=False)`. **Importing the package no longer touches the filesystem** — frontends call `setup_logging` explicitly (the CLI does, with `clean_old=True`). `pyHamilton` and `monet` logs are filtered out of the main log.

## Key Patterns

- **Abstract base classes:** `AbstractSystem` / `AbstractSystemHandler` (`orchestration/core.py`) define the subsystem contract; `hal/` defines the hardware contract.
- **Threading:** one daemon thread per subsystem handler; sync via `threading.Event`, `queue.Queue`, `threading.Lock`, and `SignalRegistry`.
- **Configuration:** instrument configs are YAML (`configs/`); protocol configs load from YAML and validate against the pydantic schema.
- **Back-compat shims:** `PycroFlow.hamilton_architecture`, `PycroFlow.protocols`, and `PycroFlow.orchestration` re-export from their new submodule homes so old import paths keep working.
- **Tests use `unittest`** with `unittest.mock`. `tests/__init__.py` installs hardware mocks and uses a tempdir for outputs (it does NOT clear `PycroFlow/TestData/`). Protocol output is pinned by snapshot regression.
