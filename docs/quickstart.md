# Quickstart

## 1. Install

PycroFlow targets Python **3.10+** on **Windows 10**. The lab Windows box
needs the hardware extras (Hamilton serial, pycromanager, nidaqmx, etc.);
a CI / developer machine only needs the dev extras.

```bash
# Lab Windows box
pip install -e .[hardware]

# Developer / CI machine
pip install -e .[dev]
```

For the Qt GUI, add the `[gui]` extra:

```bash
pip install -e .[gui]
```

`pyproject.toml` is authoritative — `setup.py` is a thin shim. `pip` will
pick up `monet` and `pycobolt` from the URLs declared there.

To put monet alongside (the GUI embeds its window in-process):

```bash
git clone git@github.com:.../monet.git ../monet
pip install -e ../monet
```

## 2. Configure logging

PycroFlow no longer touches the filesystem on import. Frontends call:

```python
import PycroFlow
PycroFlow.setup_logging(clean_old=True)
```

The `pycroflow` CLI does this for you.

## 3. Run a frontend

Interactive CLI:

```bash
pycroflow
```

Tabbed Qt GUI (needs the `[gui]` extra):

```bash
pycroflow-gui
```

The GUI has Experiment / Fluid / Imaging / Monet tabs, all sitting on the
same `services/` layer as the CLI. The Monet tab embeds monet's own window
in-process; if monet isn't installed (or is built against PyQt5 rather than
PyQt6) the tab shows a placeholder instead of failing. The GUI is import-safe
without PyQt6 — only launching it needs the `[gui]` extra.

You'll get a `cmd.Cmd`-style prompt. The typical workflow:

```
(PycroFlow) load_hamilton    # connect fluid hardware
(PycroFlow) load_imaging     # attach to Micro-Manager
(PycroFlow) load_protocol path/to/protocol.yaml
(PycroFlow) fill_tubings
(PycroFlow) start_orchestration
(PycroFlow) start_protocol
```

## 4. Drive an experiment programmatically

`example_experiment/start_experiment_240301.py` shows a complete Exchange-
PAINT script: it builds the fluid / imaging / illumination dicts, hands
them to `ProtocolBuilder.create_protocol`, and then to a
`PycroFlowInteractive` shell.

A minimal headless run on demo data:

```python
import PycroFlow
PycroFlow.setup_logging()
from PycroFlow.examples.demo_protocols import protocol
import PycroFlow.orchestration as por

po = por.ProtocolOrchestrator(protocol)
po.start_orchestration()
po.start_protocol()
```

## 5. Single-process MM Core

Only one process at a time may attach to the Micro-Manager Core. Two paths:

- **`pycroflow-gui`** embeds monet in-process and shares one Core
  (`mm_core.share_with_monet()`), so there's no conflict to begin with.
- **CLI alongside a standalone monet GUI**: PycroFlow takes a file lock at
  `%LOCALAPPDATA%\PycroFlow\mm.lock` when `ImagingSystem` initializes. If
  monet's standalone GUI is already running, `MmLockHeld` is raised with a
  clear message instead of producing a silently broken second connection.

Release the lock explicitly when done:

```python
imaging_system.close()
```

## 6. Tests

```bash
python -m unittest discover -v
```

To regenerate the protocol regression snapshots after an intended wire
change:

```bash
PYCROFLOW_UPDATE_SNAPSHOTS=1 python -m unittest \
    PycroFlow.tests.test_regression_protocols -v
```

Commit the new JSON files in `PycroFlow/tests/fixtures/snapshots/`.

## 7. Linting / CI

GitHub Actions runs `python -m unittest discover -v` on Windows / Python
3.10 for every push and PR. See `.github/workflows/tests.yml`. The
workflow installs only `.[dev]`; vendor SDKs are mocked at test-discovery
time by `PycroFlow/tests/_mock_hardware.py`.
