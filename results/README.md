# `results/` — committed performance run logs

This directory holds the machine-readable output of the WP-1 performance
harness. It is the hand-off point of the WP-1 round-trip:

1. You run the harness on the acquisition PC
   (`python -m PycroFlow.perf --instrument …`), which writes a timestamped run
   directory here (`wp1_instrument_<UTC-timestamp>/`).
2. You **commit that directory** to the branch and push (see
   [`../docs/WP-1-RUNBOOK.md`](../docs/WP-1-RUNBOOK.md)).
3. Claude analyses the committed logs here with
   `python -m PycroFlow.perf.analyze_perf results/<run-dir>` and drafts the
   go/no-go.

Each run directory contains `run_meta.json`, `metrics.csv`, and
`buffer_timeseries.csv`, whose schema is documented in
[`../docs/WP-1-perf-schema.md`](../docs/WP-1-perf-schema.md).

Emulator dry-run directories (`wp1_emulator_*`) are throwaway and normally not
committed; commit the `wp1_instrument_*` directories that carry the real
numbers.
