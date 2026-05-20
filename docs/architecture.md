# Architecture

This document maps the PycroFlow package as it stands after Stages 0–2 of
the restructuring (see `/Users/hgrabmayr/.claude/plans/please-deeply-
investigate-the-soft-waffle.md` for the full multi-stage plan).

## Layers

```
                     ┌──────────────────────┐
                     │   frontend_cli.py    │   (pycroflow command)
                     │   (future: GUI)      │
                     └──────────┬───────────┘
                                │
                 ┌──────────────▼──────────────┐
                 │       orchestration.py      │   ProtocolOrchestrator,
                 │   - signal/wait registry    │   AbstractSystem,
                 │   - per-subsystem handlers  │   AbstractSystemHandler
                 │   - threadexchange state    │
                 └─┬───────────┬──────────────┬┘
                   │           │              │
       ┌───────────▼──┐ ┌──────▼─────┐ ┌──────▼──────┐
       │ hamilton_    │ │ imaging.py │ │ illumination│
       │ architecture │ │   (MM)     │ │ .py (monet) │
       └──┬───────────┘ └──┬─────────┘ └─────────────┘
          │                │
       pyHamilton/      pycromanager
       (in-house        (vendor)
       serial driver)
```

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

`signal` appends a message to `threadexchange[target_name]`. `wait for
signal` polls that list and returns when the message appears, raises
`WaitForSignalTimeout` if the deadline expires, or returns silently on
abort / protocol-iter change.

Stage 4 of the restructuring replaces this list-based mechanism with a
proper `SignalRegistry` of `threading.Event`s and adds discriminated-union
parsing of the typed entries via pydantic.

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
- **State lives in `threadexchange`.** A dict containing `threading.Lock`,
  `threading.Event`, `queue.Queue`, and per-subsystem message `list`s.
  Stage 4 replaces this with a typed `ThreadExchange` dataclass.

## Single-process MM Core

Today PycroFlow's `ImagingSystem` and a separately-run monet GUI cannot
share Micro-Manager — the second connection silently breaks the first.
Until Stage 5 ships the in-process Qt GUI that embeds monet's
`MonetMainWindow` as a tab, an `MmCoreLock` filesystem mutex
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
| 3     | Break up monoliths + introduce HAL   | next       |
| 4     | Protocol / signal redesign           | upcoming   |
| 5     | Qt GUI + embedded monet              | upcoming   |
| 6     | Packaging & documentation polish     | upcoming   |
