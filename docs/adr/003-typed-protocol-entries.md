# 003 — Typed protocol entries via pydantic

- Status: accepted
- Stage: 2 (schema) / 4 (dispatch)

## Context

Protocol entries are dicts discriminated by a `$type` string. There was no
validation: a typo in `$type` or a missing required field surfaced only
when the orchestrator hit the entry mid-run, often after hardware had
already moved. Dispatch was a long `if/elif step['$type'].lower() == ...`
chain.

## Decision

- Stage 2: define pydantic v2 models per `$type` as a discriminated union
  (`PycroFlow/schemas/protocol_schema.py`), with `extra='allow'` so existing
  extra fields (`wait_time`, `round`, `message`, `delay`) stay valid.
  Validate in `ProtocolBuilder.create_protocol`, failing loud at build time.
- Stage 4: re-export the models from `PycroFlow/protocol_entries.py`, add
  `parse_entry` (case-insensitive on `$type`), and dispatch `run_protocol`
  via `functools.singledispatch` on the typed entry, falling back to
  `execute_protocol_entry` for entries the schema doesn't enumerate.

The wire format (YAML/dict) is unchanged — these are validation and
dispatch layers over the same data.

## Consequences

- Malformed protocols are caught at construction, not mid-run.
- Adding an entry type means adding a model + a `singledispatch.register`,
  not editing an if/elif chain.
- pydantic is a core dependency (validation must always be available).
- `incubate.duration` is `Union[float, str]` because the orchestrator
  coerces with `float()` and historical data carries both.
