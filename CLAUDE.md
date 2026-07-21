# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PycroFlow is a Python framework for coordinating microscopy image acquisition, fluid handling (Hamilton liquid handlers), and illumination control in automated fluorescence microscopy experiments (Exchange-PAINT, MERPAINT, Z-PAINT). It targets Windows 10 with hardware serial communication. Python 3.10+.

See `ARCHITECTURE.md` for the package map, `docs/architecture.md` for detail, and `docs/adr/` for the rationale behind major decisions.

## Repository & workflow

- **Active branch:** `feature-FullAutoS0A` (feature branch; PRs target `master`). This repo is one of several checked out under the `DNA-PAINT-FullAutomation` workspace — see the standing pointers below.
- **Versioning:** the version is hardcoded in `pyproject.toml` (`[project] version`) and is the single source of truth; `PycroFlow.__version__` reads it at runtime via `importlib.metadata.version("PycroFlow")` (falling back to `"0.0.0"` in an uninstalled source tree). To release, bump the number manually in `pyproject.toml` — there is no setuptools-scm / git-tag-driven versioning here.
- **Changelog:** keep `CHANGELOG.txt` current — add an entry under an `[Unreleased]` heading in every PR that changes behaviour, and promote `[Unreleased]` to a dated, version-stamped section when you bump the version.

### Standing pointers

Workspace-level planning docs live in the sibling `planning/` folder — `@`-reference them as `../../planning/…` from this repo root:

- **Playbook** — `../../planning/DNA-PAINT_ClaudeCode-Implementation-Playbook.md` (operating model, Step 0 foundations, gated work-order sequence)
- **Design doc** — `../../planning/DNA-PAINT_Automation-Recommendation.md` (the automation recommendation: initiatives, roadmap, target architecture)
- **Work-order briefs** — `../../planning/DNA-PAINT_Work-Order-Briefs.md` (paste-ready briefs S0A/S0B + WP-1…WP-16)
- **Progress tracker** — `../../planning/DNA-PAINT_Implementation-Progress-Tracker.md` (tick-off worksheet + "where we are")
- **Planning index / reading guide** — `../../planning/README.md`
- **ModuleSpec contract reference** — `../../planning/picasso-workflow_Module-Annotations_Reference.md`
- **Cross-repo contracts** (registry OpenAPI spec + published client, shared metric/workflow schemas) are produced in Step 0B and published from `picasso-registry`; record their concrete paths here once S0B lands.

## Commands

### Install
```bash
pip install -e ".[dev]"        # dev / CI (hardware libs mocked in tests)
pip install -e ".[hardware]"   # lab Windows box (real instruments)
pip install -e ".[gui]"        # PyQt6 for the `pycroflow-gui` frontend
```
Console scripts: `pycroflow` (CLI), `pycroflow-gui` (Qt GUI).
All metadata and dependencies live in `pyproject.toml`; `setup.py` is a thin shim. There is no `requirements.txt` — its former contents are fully covered by the core `dependencies` plus the `[hardware]` / `[dev]` / `[gui]` extras.

## Configuration

### Code Style
- Formatting: Black with 79-char lines. `.flake8` sets `max-line-length = 79` and `extend-ignore = E203, W503` (the Black-compatibility ignores — Black owns line wrapping, so those two pycodestyle rules stay off; note `E501` is *not* ignored here). `ruff` and `mypy` are available via the `[dev]` extra but are not yet wired into CI.
- Docstring convention: NumPy style (numpydoc) — one-line imperative
  summary, then `Parameters` / `Returns` / `Raises` / `Notes` sections with
  dashed underlines and `name : type` fields. Pair with PEP 604 type hints in
  signatures (`from __future__ import annotations`); don't restate a
  parameter's type in prose when the annotation already gives it. Matches the
  upstream `picasso` package.
- Test coverage requirement: 80%

## Testing Structure

### Run all tests
```bash
# from the repo root
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
Two levels: the **Experiment Design** (high-level intent — volumes, reservoir names, the per-type design like SPH-RESI target/RESI rounds) is compiled into the **Run Sequence** (linearized per-subsystem `$type` entry lists). `ProtocolBuilder` (`protocols/builder.py`) does the compile: `build_protocol(config)` returns the validated dict (no I/O); `create_protocol(config)` also writes the canonical YAML and returns `(fname, steps)`. Experiment types dispatch through the `EXPERIMENT_TYPES` registry (`exchange`, `merpaint`, `flushtest`, `sph-resi`).

The Experiment Design has its own pydantic schema (`schemas/experiment_design.py`, typed `Exchange` + `SPH-RESI`, hyphen aliases via `Field(alias=...)`, `validate_experiment_design`). Fields carry editor metadata in `Field(json_schema_extra=...)` (helpers `_field(...)` / `_unit(...)`; read back via `field_meta` / `field_unit`) — all advisory (no effect on validation), consumed by the schema-driven editor: `unit` (volumes `µl`, velocities `µl/min`, delays `s`, incubations `min`, exposure `ms`, laser power `mW`); `choices` / `choices_from` + `allow_none` (dropdowns — e.g. `mode` is a fixed list; imager/buffer/blocker/adapter fields are dropdowns of the design's reservoir *names*; `illu.settings.laser` is a dropdown of the setup's monet laser lines; a `list[scalar]` with `choices_from` (Exchange `imagers`) becomes add/remove dropdown rows like the RESI-rounds, with optional `title` (group-box name, e.g. "rounds") and `row_label` (per-row template, e.g. "imager round {}", renumbered on add/remove)); `tooltip`; and for mappings `columns` / `display_value_first` / `key_choices_from` / `value_choices_from`. `SchemaForm(..., context=..., skip_fields=...)` threads an observable `FormContext` of dropdown options (`reservoir_names` from the design, `reservoir_ids` from the loaded setup via `SystemService.reservoir_ids()`, `lasers` from the setup's monet config via `SystemService.laser_options()` — both through the design tab's provider callables) down the form tree and omits a union variant's `type` (shown by the selector). The `reservoir_names` table publishes its values live (`provides='reservoir_names'`), so the imager/buffer dropdowns refresh as you edit it. `reservoir_names` / `special_names` render as labelled (ID, name) tables (the latter stored name→id but displayed id-first); reservoir ids are restricted to the setup's manifold. In `FluidSettings` the reservoir tables come first, then volumes/cleaning; the wash buffers are **not** duplicated there — they live only in the `experiment` block (the SPH-RESI builder reads `experiment['wash_buffer_1'/'wash_buffer_2']`). Scalar form labels are left-aligned (`QFormLayout.setLabelAlignment`). — it's the single source of truth for both builder/GUI validation and the schema-driven editor. The Run Sequence is pinned by `schemas/protocol_schema.py` (discriminated union, `extra='allow'`); `protocol_entries.py` exposes the typed models + `parse_entry` / `parse_protocol`.

### Fluid automation (`fluid/` + `pyHamilton/` + `hal/`)
`LegacyArchitecture` (`fluid/legacy.py`) drives Hamilton MVP valves and PSD syringe pumps over serial. Instrument topology lives in `configs/legacy_system.yaml` and `configs/legacy_tubing.yaml`, loaded by `configs/__init__.py` and re-exported as `legacy_system_config` / `legacy_tubing_config`. `pyHamilton/` is the in-house serial driver (`SerialBus` in `communication.py`, `command.py`, `mvp.py`, `psd.py`). `hal/` defines vendor-neutral `Pump` / `Valve` / `SpillSensor` ABCs.

**ibidi MultiFlOW multiplexer** (`ibidi_multiplexer.py`) is an alternative to the Hamilton MVP rotary valves for reservoir multiplexing (the syringe pumps stay Hamilton). It's a standalone 24-channel bi-stable-valve actuator on its **own** USB serial port; `IbidiMultiplexer` implements the `hal/` `Valve` ABC and presents `set_valve(channel)` (atomic exclusive open via `SETBATCHVALVES`) so `LegacyArchitecture._set_valves` drives it unchanged. A setup wires it with an optional `hamilton.ibidi:` block (`port`/`baud`/`channels`/`address`) and reservoirs whose `valve_pos` is `{ibidi: <channel>, 1: in}` (see `configs/setups/IbidiEmulator.yaml`, a 24-reservoir emulated setup). The serial-level emulator is `tests/emulators/ibidi_serial.py` (`FakeIbidiSerial` / `patch_ibidi_serial`), patched alongside `patch_serial` in `SystemService.connect_fluid` for emulated setups.

**Per-microscope setups** live in `configs/setups/<name>.yaml` (e.g. `Mercury`, `Emulator`, `IbidiEmulator`) — the fixed hardware (interface, valves, pumps, flush_pos, full reservoir manifold, tubing, PFS tags) plus the `setup` name (a `monet.CONFIGS` key). `configs.load_setup(name)` / `list_setups()` load them; `configs.assemble_hamilton_config(setup, fluid_settings)` merges a setup's manifold with an Experiment Design's `reservoir_names` / `special_names` into the `hamilton_config` `LegacyArchitecture` expects. The `Emulator` setup (`emulated: true`) makes `SystemService.connect_*` build the *real* drivers over `tests/emulators` (`patch_serial` fake serial for fluid; `EmulatedImaging/IlluminationSystem`) so the whole app runs with no instruments.

### Imaging (`imaging.py` + `services/mm_core.py` + `mm_lock.py`)
`ImagingSystem` wraps pycromanager for acquisition and PFS monitoring. The MM Core/Studio singletons are owned by `services/mm_core.py` (supersedes `util.PyMgrSingleton`). `mm_lock.MmCoreLock` is a filesystem mutex that prevents PycroFlow imaging and a standalone monet GUI from attaching to MM simultaneously (raises `MmLockHeld`).

### Illumination (`illumination.py`)
`IlluminationSystem` manages laser power/wavelength via **monet**, which is an external sibling repository (not vendored — see `docs/adr/004`). Tests mock it. The monet config name is the **microscope setup** name (a `monet.CONFIGS` key), passed to `IlluminationSystem(setup=...)` by `SystemService.connect_illumination` — *not* carried in the experiment design. The Experiment Design only holds illumination **intent** (`illu.settings`: laser, power_acq/nonacq in mW, warmup, shutter); it has no `illu.parameters` (monet provides the per-microscope calibration; the old `channel_group`/`filter`/`ROI` were unused). monet loads lazily on first laser use (`_ensure_monet`).

### Services (`services/`)
Frontend-agnostic layer both the CLI and the Qt GUI consume: `ExperimentService` (lifecycle + observer hooks), `SystemService` (manual hardware control), `mm_core` (Core ownership).

### Spill sensor (`spill_sensor_arduino.py`)
`ArduinoSensorInterface` polls an Arduino over serial for wetness/spill detection in a background thread. Port via the `PYCROFLOW_SPILL_PORT` env var.

### Frontends (`frontend_cli.py`, `gui/`)
`PycroFlowInteractive` (`cmd.Cmd`) is the `pycroflow` console entry point; lifecycle commands route through `services/`. `gui/` is the `pycroflow-gui` PyQt6 frontend (`[gui]` extra): a `PycroFlowMainWindow` with a toolbar (setup selector + Connect + run controls) over tabs (**Experiment Design / Run Sequence / Fluid / Imaging / Monet**), all on the same `services/` layer. The toolbar holds only the setup selector + Connect; the run controls (Load run sequence, Start, a single Pause/Resume toggle, Abort) live in the Run Sequence tab, enabled/relabelled per experiment state. The window title shows the package version (`PycroFlow <__version__>`). The **microscope setup** is chosen in the toolbar combo (drives the Monet tab); subsystems **autoconnect** once an experiment design is loaded — the main window is the connection coordinator (`_connect_system`/`_autoconnect`, fluid/illumination in background workers, imaging on the GUI thread for MM/ZMQ safety) and mirrors the connected systems into `ExperimentService`. Each subsystem tab shows its connection status + a manual Connect/Reconnect (illumination status lives in the Monet tab). While an experiment is running (ORCHESTRATING/RUNNING/PAUSED) the manual hardware controls — setup selector, per-tab connect, fluid manual ops, and the embedded monet GUI — are disabled so they can't fight the orchestrator for the instruments (the fluid emergency STOP stays enabled). **Experiment Design** is a schema-driven structured editor (`gui/widgets/schema_form.py`, generated from the `ExperimentDesign` pydantic model) with Load/Save/Translate (loading a design *from a file* `os.chdir`s to its folder so run outputs land beside it); **Run Sequence** shows the compiled steps in three side-by-side per-subsystem lists (fluid/img/illu) with a single editable parameter box below (labelled with the last-clicked step's list); clicking a step selects + centres the concurrent step in the other two lists (traced via the signal/wait-for-signal happens-before graph → longest-path "logical levels"), and a "Center on current step" button (enabled only while running) scrolls all three lists to the running step, live progress bars (overall + rounds + within-current-round steps + per-subsystem within-step bars for imaging frames, fluid incubation waits, and fluid inject/pump-out — the latter a volume/velocity time estimate, not pump polling — fed by `ExperimentService.step_progress()` → each handler's `get_step_progress()`), and step shading. Protocol/design YAML can be drag&dropped onto their tabs (`gui/widgets/dnd.py`). Long blocking calls run via `gui/widgets/worker.py` (`run_in_background`) so the UI never freezes. The Monet tab embeds monet's `MonetWidget(initial_microscope=<setup>)` (falls back to `MonetMainWindow`, then a placeholder if monet is absent/mocked/a non-PyQt6 `QWidget`). `gui/qt_bridge.py` marshals service observer callbacks onto the GUI thread as Qt signals. The package is import-safe without PyQt6 — Qt is imported lazily so `import PycroFlow.gui` and the test suite work without the `[gui]` extra.

### Logging
loguru, configured by `PycroFlow.setup_logging(clean_old=False)`. **Importing the package no longer touches the filesystem** — frontends call `setup_logging` explicitly (the CLI does, with `clean_old=True`). `pyHamilton` and `monet` logs are filtered out of the main log.

## Key Patterns

- **Abstract base classes:** `AbstractSystem` / `AbstractSystemHandler` (`orchestration/core.py`) define the subsystem contract; `hal/` defines the hardware contract.
- **Threading:** one daemon thread per subsystem handler; sync via `threading.Event`, `queue.Queue`, `threading.Lock`, and `SignalRegistry`.
- **Configuration:** instrument configs are YAML (`configs/`); protocol configs load from YAML and validate against the pydantic schema.
- **Back-compat shims:** `PycroFlow.hamilton_architecture`, `PycroFlow.protocols`, and `PycroFlow.orchestration` re-export from their new submodule homes so old import paths keep working.
- **Tests use `unittest`** with `unittest.mock`. `tests/__init__.py` installs hardware mocks and uses a tempdir for outputs (it does NOT clear `PycroFlow/TestData/`). Protocol output is pinned by snapshot regression.
