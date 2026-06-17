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
- [docs/confluence/](docs/confluence/) — ready-to-upload Confluence pages
  (PycroFlow overview, and the lab experiment workflow)
- [Running an experiment in the lab](#running-an-experiment-in-the-lab) — the
  full procedure, below

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

## Launching the frontends

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

## Running an experiment in the lab

A run is driven from one `pycroflow` CLI session that coordinates fluid
exchange, acquisition, and illumination. The detailed, illustrated procedure
lives in [docs/confluence/running-an-experiment.confluence.html](docs/confluence/running-an-experiment.confluence.html);
the short version:

1. **Prepare.** Passivate the reservoir tubes (DNA low-bind tubes; for the DyBE
   DNA-PAINT flavour, first incubate with antibody-incubation buffer, then
   passivate with imaging buffer + 0.1% BSA + 0.1% Tween-20 — for other flavours
   the antibody-incubation step is skipped), and prepare one imaging mix per
   round. Power on the pumps and the microscope; configure Micro-Manager and
   monet as for a manual experiment.
2. **Configure.** Copy `start_experiment.py` from a previous run into the data
   folder and edit the settings block: `experiment_name`, `reservoir_names`
   (position → solution; reservoir 6 is the wash buffer by convention),
   `initial_target`, `target_sequence`, `exposure_time`, `n_frames`,
   `use_mm_positions`, `laser`, `sample_power`. See
   [`example_experiment/start_experiment_240301.py`](example_experiment/start_experiment_240301.py).
3. **Launch and prime.** Start the CLI (`pycroflow`, or the `execute pycroflow`
   shortcut). It auto-generates a timestamped protocol YAML. Run `fill_tubings`
   to passivate/prime the lines, load the imager tubes (positions matching
   `reservoir_names`), `fill_tubings` again, then `start_orchestration`.
4. **(Optional) tweak per target.** Edit the generated YAML (e.g. `frames` on an
   `img`/`acquire` step, or `set power` on an `illu` step) and
   `load_protocol <file>.yaml`.
5. **Run.** Connect the input (blue) and output (red) needles to the sample,
   mount it, `find positions`, then `start_protocol`. Use
   `pause_protocol` / `resume_protocol` / `abort_protocol` as needed.
6. **Clean up.** With both needles in a shared waste tube, run `clean_tubings`
   (see `help clean_tubings`), then `exit`, lasers off in monet, power down.

### CLI commands

| Command | Purpose |
|---------|---------|
| `fill_tubings` | Prime/passivate tubings from their reservoirs |
| `start_orchestration` | Start the per-subsystem threads |
| `load_protocol <file>.yaml` | Load a (possibly hand-edited) protocol |
| `start_protocol` | Run the loaded protocol (optional start entries) |
| `pause_protocol` / `resume_protocol` / `abort_protocol` | Control a running protocol |
| `get_protocol_iter` / `set_protocol_iter` | Inspect / set the current step per subsystem |
| `deliver` / `inject` / `set_valves` / `pump` | Manual fluid operations |
| `laser` / `power` | Manual illumination control |
| `clean_tubings` | Clean all tubings (`extra_vol`, `cleaning_reservoirs`, `mode`, `max_reservoir_vol`) |
| `help` / `help <command>` | List commands / detailed help |
| `exit` | Stop orchestration and leave the CLI |

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
