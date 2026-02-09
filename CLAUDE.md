# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PycroFlow is a Python framework for coordinating microscopy image acquisition, fluid handling (Hamilton liquid handlers), and illumination control in automated fluorescence microscopy experiments (Exchange-PAINT, MERPAINT, Z-PAINT). It targets Windows 10 with hardware serial communication.

## Commands

### Install
```bash
pip install -e .
pip install -r requirements.txt
```

### Run all tests
```bash
cd /Users/hgrabmayr/GitHub/PycroFlow
python -m unittest -v
```

### Run a single test file
```bash
python -m unittest PycroFlow.tests.test_protocols -v
```

### Run a single test case
```bash
python -m unittest PycroFlow.tests.test_protocols.TestProtocolBuilder.test_method_name -v
```

### Run monet subsystem tests
```bash
python -m unittest discover -s PycroFlow/monet/tests -v
```

There are no configured linters or formatters.

## Architecture

### Orchestration Layer (`orchestration.py`)
The central coordinator. `ProtocolOrchestrator` manages multiple `AbstractSystemHandler` subclasses (FluidHandler, ImagingHandler, IlluminationHandler) running in separate threads. Handlers communicate via thread-safe queues and locks using a signal protocol — protocol entries with `$type: 'signal'` and `$type: 'wait for signal'` synchronize across subsystems. Supports pause/resume/abort.

### Protocol System (`protocols.py`)
`ProtocolBuilder` transforms high-level experiment descriptions (exchange type, imaging rounds, reagents) into per-subsystem protocol entry lists. Each entry is a dict with a `$type` field:
- **Fluid:** `inject`, `incubate`, `flush`, `wait for signal`, `signal`
- **Imaging:** `acquire`, `wait for signal`, `signal`
- **Illumination:** power adjustments, signal-based coordination

### Fluid Automation (`hamilton_architecture.py` + `pyHamilton/`)
`LegacyArchitecture` drives Hamilton MVP valves and PSD syringe pumps over serial (pyserial). Configured via `legacy_system_config` and `legacy_tubing_config` dicts. The `pyHamilton/` subpackage is a custom serial driver (`command.py` for command interface, `communication.py` for serial I/O, `mvp.py`/`psd.py` for device-specific commands).

### Imaging (`imaging.py`)
`ImagingSystem` wraps pycromanager (Micro-Manager) for frame acquisition, multi-dimensional acquisition, and PFS (Perfect Focus System) monitoring. Uses `PyMgrSingleton` for Micro-Manager Core/Studio instances.

### Illumination (`illumination.py` + `monet/`)
`IlluminationSystem` manages laser power/wavelength via the MONET subsystem. MONET handles laser control, attenuation (Kinesis devices), beam path switching, power measurement (Thorlabs), and calibration.

### Spill Sensor (`spill_sensor_arduino.py`)
`ArduinoSensorInterface` communicates with Arduino over serial for wetness/spill detection. Runs monitoring in a background thread with `@run_threaded` decorator.

### Frontend (`frontend_cli.py`)
`PycroFlowInteractive` is a `cmd.Cmd`-based interactive CLI for loading protocols, filling tubings, starting/stopping orchestration, and system cleanup.

### Logging
Uses loguru (configured in `__init__.py`). Logs rotate at 1MB with 5 backups. The `pyHamilton` and `monet` subpackages are filtered out of the main log. Old log files are deleted on import.

## Key Patterns

- **Abstract base classes:** `AbstractSystem` and `AbstractSystemHandler` (in `orchestration.py`) define the contract for all subsystems.
- **Threading model:** Each subsystem handler runs its protocol in a dedicated thread. Inter-thread sync uses `threading.Event`, `queue.Queue`, and `threading.Lock`.
- **Configuration:** System and tubing configs are Python dicts (not files). Protocol configs can be loaded from YAML.
- **Tests use `unittest`** with `unittest.mock` for hardware mocking. The test `__init__.py` creates/clears a `PycroFlow/TestData/` directory on test suite init.
