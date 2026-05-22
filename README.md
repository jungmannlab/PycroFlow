# PycroFlow

Python framework for coordinating automated fluorescence microscopy
experiments — image acquisition (Micro-Manager / pycromanager), fluid
handling (Hamilton MVP valves and PSD syringe pumps), and illumination
control (via the [monet](https://github.com/) sibling project).

Targets Windows 10 with hardware serial communication. Used in production
for Exchange-PAINT, MERPAINT, and Z-PAINT experiments.

## Documentation

- [docs/quickstart.md](docs/quickstart.md) — installation, first protocol, CLI walkthrough
- [docs/architecture.md](docs/architecture.md) — module layout, subsystem
  abstractions, signal/wait protocol, threading model

## Install

```bash
# Lab Windows box (real hardware)
pip install -e .[hardware]

# Developer machine / CI (mocked hardware)
pip install -e .[dev]

# Qt GUI frontend
pip install -e .[gui]
```

Python 3.10+. All dependencies live in `pyproject.toml`.

## Running an experiment

```bash
# Interactive CLI
pycroflow

# Tabbed Qt GUI (Experiment / Fluid / Imaging / Monet)
pycroflow-gui

# or, programmatically
python example_experiment/start_experiment_240301.py
```

The GUI's Monet tab embeds monet's own window in-process, so PycroFlow
imaging and monet share one Micro-Manager connection (no two-process
conflict).

## Tests

```bash
python -m unittest discover -v
```

The conftest in `PycroFlow/tests/_mock_hardware.py` stubs out vendor SDKs
(pycromanager, monet, pycobolt, nidaqmx) so the suite runs anywhere; real
SDKs are preferred when present.

## Layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full package map (the code is
organized into `orchestration/`, `protocols/`, `fluid/`, `hal/`, `services/`,
`gui/`, `schemas/`, `configs/`, and `examples/`, with back-compat shims at the
old `orchestration.py` / `protocols.py` / `hamilton_architecture.py` import
paths).

## License

MIT (declared in `pyproject.toml`).
