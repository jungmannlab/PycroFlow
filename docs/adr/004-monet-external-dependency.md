# 004 — monet as an external sibling dependency

- Status: accepted
- Stage: 0 / 2

## Context

`CLAUDE.md` and the `__init__.py` log filter referenced a `PycroFlow/monet/`
subpackage and a `PycroFlow/monet/tests` directory that do not exist in this
repository. monet (laser/illumination control) actually lives in a separate
sibling repository.

## Decision

Treat monet as a normal external dependency, installed from its own repo.
`illumination.py` imports `monet` / `monet.control` at module scope and the
test suite mocks them when absent (`PycroFlow/tests/_mock_hardware.py`).
Remove the stale `PycroFlow/monet/...` references from docs.

## Consequences

- No vendoring; monet evolves independently and is version-pinned at the
  install boundary.
- Dev/CI environments don't need monet installed — it's mocked.
- Stage 5 (deferred Qt GUI) will embed monet's `MonetMainWindow` in-process;
  see ADR 006 for the Core-sharing implication.
