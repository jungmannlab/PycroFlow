# 005 — Frontend-agnostic service layer

- Status: accepted
- Stage: 3

## Context

`frontend_cli.py` was the only frontend and reached into orchestrator and
system internals directly (e.g. `self.fluid_system._pump`,
`self.orchestrator.execute_system_function(...)`). A second frontend (the
planned Qt GUI) would either duplicate that coupling or require it to be
untangled first.

## Decision

Add `PycroFlow/services/`:

- `ExperimentService` wraps `ProtocolOrchestrator` and exposes
  load / start / pause / resume / abort / status plus observer hooks
  (state-change and log callbacks) — frontend-agnostic, so the GUI's
  `qt_bridge` can translate observer calls into Qt signals.
- `SystemService` wraps the fluid/imaging/illumination systems for the
  manual-control commands the CLI exposes, replacing private-attribute
  reach-through with real methods.
- `mm_core` owns the Micro-Manager Core/Studio singletons (see ADR 006).

`frontend_cli` was migrated to route lifecycle commands and the `_pump`
reach-through through these services; remaining commands still use direct
access and can be migrated incrementally.

## Consequences

- Both the CLI and the Qt GUI (ADR 007) sit on one API.
- Orchestrator stays focused on threading/signaling.
- Observer hooks decouple UI updates from orchestration internals.
