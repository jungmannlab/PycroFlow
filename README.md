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
```

Python 3.10+. All dependencies live in `pyproject.toml`.

## Running an experiment

```bash
# Interactive CLI
pycroflow

# or, programmatically
python example_experiment/start_experiment_240301.py
```

## Tests

```bash
python -m unittest discover -v
```

The conftest in `PycroFlow/tests/_mock_hardware.py` stubs out vendor SDKs
(pycromanager, monet, pycobolt, nidaqmx) so the suite runs anywhere; real
SDKs are preferred when present.

## Layout

```
PycroFlow/
├── orchestration.py        # ProtocolOrchestrator + handler threads
├── protocols.py            # ProtocolBuilder (high-level → per-subsystem steps)
├── hamilton_architecture.py    # Legacy fluid system
├── imaging.py              # ImagingSystem (pycromanager wrapper)
├── illumination.py         # IlluminationSystem (monet wrapper)
├── frontend_cli.py         # `pycroflow` interactive CLI
├── mm_lock.py              # MM-Core single-process guard
├── pyHamilton/             # In-house Hamilton serial driver
├── configs/                # YAML instrument configs
├── schemas/                # Pydantic protocol-wire-format schema
├── examples/               # Demo protocols + REPL snippets
└── tests/                  # unittest suite + fixtures + snapshots
```

## License

MIT (see `setup.py`).
