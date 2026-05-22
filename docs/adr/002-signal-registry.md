# 002 — Event-backed SignalRegistry

- Status: accepted
- Stage: 4 (timeout groundwork in Stage 1)

## Context

Cross-subsystem synchronization (`signal` / `wait for signal` protocol
entries) was implemented by appending strings to a per-subsystem
`list[str]` and busy-polling that list every 50 ms with substring matching.
Two problems: a typo in a `wait for signal` value hung the orchestrator
forever (no timeout), and the poll burned CPU and could miss a signal
delivered while the list was mutated under contention.

## Decision

- Stage 1: add a hard timeout to `wait_xchange` (`WaitForSignalTimeout`),
  defaulting to 4 h, overridable per protocol entry.
- Stage 4: add `SignalRegistry` — a `(target, value) -> threading.Event`
  map with `fire` / `wait(timeout)` / `is_set`. `send_message` fires the
  matching event; `wait_xchange` waits on it via `Event.wait()` so a waiter
  wakes the instant the producer fires.

The legacy list-of-strings path is preserved in parallel: it still backs
substring-prefixed entries (e.g. `start entry: 3`) that the registry can't
represent, and `wait_xchange` falls back to a list scan.

## Consequences

- No more indefinite hangs; no busy-poll for the common case.
- Fire-before-register is safe — the Event persists.
- Both paths coexist, so existing protocol YAMLs are unchanged.
- Follow-up: retire the list path once all callers use named signals.
