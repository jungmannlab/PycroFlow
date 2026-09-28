"""WP-1 live-reader performance harness.

Benchmarks whether concurrent *incremental NDTiff reading* compromises an
acquisition running at the target frame rate — the Phase-1 go/no-go between
**live streaming** (read the dataset Micro-Manager is already writing) and
**post-FOV batch** processing.

The harness is deliberately split so the *measurement* is identical in both
modes and only the **frame source** differs:

``emulator``
    A pure-stdlib producer/consumer/bounded-buffer simulation
    (:class:`~PycroFlow.perf.backends.EmulatedBackend`). This is the dry run
    that Claude can validate in CI with no instrument.
``instrument``
    A real pycromanager acquisition + incremental NDTiff ``Dataset`` read
    (:class:`~PycroFlow.perf.backends.InstrumentBackend`), run by hand on the
    Windows acquisition PC.

Everything the harness records — Micro-Manager circular-buffer occupancy
(time series + peak), dropped-frame count, and write throughput, swept over
live-evaluation batch sizes — is computed by the *same* code
(:mod:`PycroFlow.perf.harness`) in both modes. The output schema
(:mod:`PycroFlow.perf.schema`) is stable and documented so the analysis step
(:mod:`PycroFlow.perf.analyze_perf`) is reproducible without Claude present at
runtime.

See ``docs/WP-1-RUNBOOK.md`` for how to run it on the acquisition PC and
``docs/WP-1-perf-schema.md`` for the output schema + go/no-go thresholds.
"""

from __future__ import annotations

from PycroFlow.perf.config import EmulatorParams, PerfConfig, load_config
from PycroFlow.perf.schema import SCHEMA_VERSION

__all__ = [
    "PerfConfig",
    "EmulatorParams",
    "load_config",
    "SCHEMA_VERSION",
]
