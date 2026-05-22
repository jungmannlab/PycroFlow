# Architecture

This document maps the PycroFlow package as it stands after the
restructuring (Stages 0–5, see `/Users/hgrabmayr/.claude/plans/please-
deeply-investigate-the-soft-waffle.md` for the full plan).

## Layers

```
        ┌──────────────────────┐   ┌──────────────────────┐
        │   frontend_cli.py    │   │   gui/ (PyQt5)       │  Monet tab
        │   (pycroflow)        │   │   (pycroflow-gui)    │  embeds monet
        └──────────┬───────────┘   └──────────┬───────────┘
                   └────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │          services/          │   ExperimentService,
                 │   (frontend-agnostic API)   │   SystemService, mm_core
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │       orchestration/        │   ProtocolOrchestrator,
                 │   - SignalRegistry          │   AbstractSystem,
                 │   - per-subsystem handlers  │   AbstractSystemHandler
                 │   - ThreadExchange state    │
                 └─┬───────────┬──────────────┬┘
                   │           │              │
       ┌───────────▼──┐ ┌──────▼─────┐ ┌──────▼──────┐
       │ fluid/legacy │ │ imaging.py │ │ illumination│
       │              │ │   (MM)     │ │ .py (monet) │
       └──┬───────────┘ └──┬─────────┘ └─────────────┘
          │                │
       pyHamilton/      pycromanager
       (in-house        (vendor)
       serial driver)
```

Both frontends sit on the `services/` layer. The GUI's Monet tab embeds
monet's `MonetMainWindow` in-process so the two share one Micro-Manager
Core (see the MM-Core section below and ADR 006).

## Subsystems

Every subsystem inherits from `orchestration.AbstractSystem` and runs in
its own thread via a matching `AbstractSystemHandler`:

| Handler              | System                | Backed by                                    |
| -------------------- | --------------------- | -------------------------------------------- |
| `FluidHandler`       | `LegacyArchitecture`  | `pyHamilton/` + `hamilton_components.py`     |
| `ImagingHandler`     | `ImagingSystem`       | `pycromanager` (Micro-Manager Core / Studio) |
| `IlluminationHandler`| `IlluminationSystem`  | `monet` (sibling repo)                       |

Handlers are daemon threads sharing a `threadexchange` dict for locks,
events, and message lists.

## Signal protocol

Cross-subsystem synchronization uses protocol entries:

```yaml
- {$type: signal, value: "fluid round 1 done"}
- {$type: wait for signal, target: img, value: "round 1 done"}
- {$type: wait for signal, target: fluid, value: "round 2 done", timeout: 600}
```

`signal` appends a message to `threadexchange[target_name]` and fires the
matching `SignalRegistry` event; `wait for signal` waits on that event
(`Event.wait(timeout)`), falling back to a list scan for legacy
substring-prefixed entries. It raises `WaitForSignalTimeout` if the
deadline expires, or returns silently on abort / protocol-iter change.

The `SignalRegistry` of `threading.Event`s (Stage 4) replaced the original
busy-poll, and protocol entries are parsed into a pydantic
discriminated-union and dispatched via `functools.singledispatch`.

## Configuration

| What                                  | Lives at                                    |
| ------------------------------------- | ------------------------------------------- |
| Instrument topology (legacy system)   | `PycroFlow/configs/legacy_system.yaml`      |
| Tubing volumes                        | `PycroFlow/configs/legacy_tubing.yaml`      |
| Protocol wire-format schema           | `PycroFlow/schemas/protocol_schema.py`      |
| Demo protocols / REPL examples        | `PycroFlow/examples/demo_protocols.py`      |
| Test fixtures (input configs)         | `PycroFlow/tests/fixtures/configs/*.py`     |
| Regression snapshots                  | `PycroFlow/tests/fixtures/snapshots/*.json` |
| Output protocol files (per run)       | `<save_dir>/<base_name>_YYMMDD-HHMM.yaml`   |

`hamilton_architecture.py` re-exports `legacy_system_config` and
`legacy_tubing_config` as module attributes by loading the YAML at import
time, preserving back-compat with existing call sites.

## Wire format

Per-subsystem protocol entries are dicts keyed by `$type`. The current
catalog (see `schemas/protocol_schema.py`):

| `$type`             | Subsystem | Required fields                          |
| ------------------- | --------- | ---------------------------------------- |
| `inject`            | fluid     | `reservoir_id`, `volume`                 |
| `incubate`          | fluid     | `duration` (float-or-str)                |
| `flush`             | fluid     | `flushfactor`                            |
| `pump_out`          | fluid     | `volume`                                 |
| `await_acquisition` | fluid     | —                                        |
| `signal`            | any       | `value` (optional `target`)              |
| `wait for signal`   | any       | `target`, `value` (optional `timeout`)   |
| `acquire`           | img       | `frames`, `t_exp`                        |
| `power`             | illu      | `value`                                  |
| `set power`         | illu      | `laser`, `power`                         |
| `set shutter`       | illu      | `state`                                  |
| `laser enable`      | illu      | `laser`, `state`                         |

Entries may carry extra fields; the schema uses `extra='allow'`. The
schema fires at `ProtocolBuilder.create_protocol` so a typo in `$type` is
caught at construction, not mid-run.

## Threading model

- **Orchestrator owns the threads.** `ProtocolOrchestrator.start_orchestration`
  starts one handler thread per subsystem; each runs until `abort_flag` or
  `graceful_stop_flag` fires.
- **All handlers are daemons.** `Ctrl-C` or interpreter exit reliably
  terminates them; the previous non-daemon flag could leave zombies.
- **State lives in `ThreadExchange`.** A per-instance object (Stage 4,
  replacing a shared class-level dict) holding `threading.Lock`,
  `threading.Event`, `queue.Queue`, per-subsystem message `list`s, and the
  `SignalRegistry`. It subclasses `dict`, so `txchange['fluid_lock']` and
  the typed `txchange.fluid_lock` both work.

## Single-process MM Core

PycroFlow's `ImagingSystem` and a separately-run monet GUI cannot share
Micro-Manager across two processes — the second connection silently breaks
the first. Two mechanisms address this:

1. **In-process GUI (Stage 5).** The `pycroflow-gui` Qt frontend embeds
   monet's `MonetMainWindow` as a tab and calls
   `services.mm_core.share_with_monet()`, so PycroFlow imaging and monet use
   one Core inside one process — the conflict is gone structurally.
2. **Lockfile guard (Stage 1).** For users still running the CLI alongside a
   standalone monet GUI, an `MmCoreLock` filesystem mutex
   (`%LOCALAPPDATA%\PycroFlow\mm.lock` on Windows, `~/.cache/PycroFlow/mm.lock`
   on POSIX) catches the conflict at `ImagingSystem.__init__` and raises
   `MmLockHeld` with a clear error.

## Logging

`loguru`-based, configured by `PycroFlow.setup_logging()`. Importing
PycroFlow no longer touches the filesystem (the old import-time call to
`rem_old_logfiles()` was removed). Frontends decide whether to clean
rotated logs (`clean_old=True`).

## Restructuring stages

| Stage | Title                                | Status     |
| ----- | ------------------------------------ | ---------- |
| 0     | Safety net (CI, snapshot, packaging) | ✅ shipped |
| 1     | Reliability fixes                    | ✅ shipped |
| 2     | Decouple config & data from code     | ✅ shipped |
| 3     | Break up monoliths + introduce HAL   | ✅ shipped |
| 4     | Protocol / signal redesign           | ✅ shipped |
| 5     | Qt GUI + embedded monet              | ✅ shipped |
| 6     | Packaging & documentation polish     | ✅ shipped |

Deferred follow-ups (tracked in module docstrings): full extraction of
`fluid/calibration.py` / `fluid/protocol_exec.py` and the per-experiment
`protocols/*.py` step builders from their host classes; migrating the
remaining `frontend_cli` commands onto `services/`; harmonizing
`create_steps_flushtest` to the nested config schema.
