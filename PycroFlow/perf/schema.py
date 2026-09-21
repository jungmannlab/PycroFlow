"""Stable, documented output schema for the WP-1 performance harness.

Every run of the harness writes one timestamped directory holding exactly
three files, whose shapes are pinned here so the analysis step
(:mod:`PycroFlow.perf.analyze_perf`) is reproducible without Claude present at
runtime:

``run_meta.json``
    Environment + provenance (host, OS, Python / PycroFlow / pycromanager
    versions, camera / MM version when available, mode, frame rate, buffer
    size, sweep list, git commit, UTC start / end) plus the fully-resolved
    config. Top-level keys are pinned by :data:`RUN_META_REQUIRED_KEYS`.
``metrics.csv``
    One row per (mode x configuration). Columns pinned by
    :data:`METRICS_COLUMNS`.
``buffer_timeseries.csv``
    Circular-buffer occupancy vs frame index, one row per monitor sample.
    Columns pinned by :data:`TIMESERIES_COLUMNS`.

The schema is versioned by :data:`SCHEMA_VERSION`; bump it on any incompatible
change and update ``docs/WP-1-perf-schema.md``.
"""

from __future__ import annotations

import csv
import json
import os

SCHEMA_VERSION = "1.1"

RUN_META_FILE = "run_meta.json"
METRICS_FILE = "metrics.csv"
TIMESERIES_FILE = "buffer_timeseries.csv"

# --- metrics.csv --------------------------------------------------------
# One row per configuration. ``reader`` is False for the baseline run;
# ``batch_size`` is 0 for the baseline (reader off).
METRICS_COLUMNS = [
    "mode",
    "reader",
    "batch_size",
    "n_frames",
    "frame_rate_hz",
    "buffer_mb",
    "buffer_frames",
    "frame_bytes",
    "frames_produced",
    "frames_written",
    "dropped_count",
    "dropped_fraction",
    "occupancy_peak",
    "occupancy_mean",
    "throughput_fps",
    "duration_s",
]

# --- buffer_timeseries.csv ---------------------------------------------
# One row per occupancy sample; tagged with the configuration it belongs to.
TIMESERIES_COLUMNS = [
    "mode",
    "reader",
    "batch_size",
    "sample_index",
    "t_rel_s",
    "frame_index",
    "occupancy",
]

# --- run_meta.json ------------------------------------------------------
RUN_META_REQUIRED_KEYS = [
    "schema_version",
    "mode",
    "host",
    "os",
    "python_version",
    "pycroflow_version",
    "frame_rate_hz",
    "n_frames",
    "buffer_mb",
    "batch_sizes",
    "git_commit",
    "utc_start",
    "utc_end",
    "config",
    "backend",
]

# --- go/no-go thresholds ------------------------------------------------
# Documented decision thresholds for "does the live reader compromise
# acquisition?". A with-reader configuration PASSES when all three hold vs.
# its same-mode no-reader baseline. See docs/WP-1-perf-schema.md for the
# rationale. Analysis may override these from the command line.
#
# MAX_DROPPED_FRACTION
#     Any dropped frames are disqualifying for a DNA-PAINT kinetics stream;
#     a tiny non-zero tolerance absorbs a single boundary artefact.
# MIN_THROUGHPUT_RETENTION
#     With-reader write throughput must stay within 5% of the baseline.
# MAX_OCCUPANCY_FRACTION
#     Peak circular-buffer occupancy must stay below half of capacity, i.e.
#     comfortable head-room before overflow.
MAX_DROPPED_FRACTION = 0.001
MIN_THROUGHPUT_RETENTION = 0.95
MAX_OCCUPANCY_FRACTION = 0.5

DEFAULT_THRESHOLDS = {
    "max_dropped_fraction": MAX_DROPPED_FRACTION,
    "min_throughput_retention": MIN_THROUGHPUT_RETENTION,
    "max_occupancy_fraction": MAX_OCCUPANCY_FRACTION,
}


def write_metrics(path: str, rows: list[dict]) -> None:
    """Write ``metrics.csv`` with the pinned columns."""
    _write_csv(path, METRICS_COLUMNS, rows)


def write_timeseries(path: str, rows: list[dict]) -> None:
    """Write ``buffer_timeseries.csv`` with the pinned columns."""
    _write_csv(path, TIMESERIES_COLUMNS, rows)


def init_run_csvs(run_dir: str) -> None:
    """Create ``metrics.csv`` / ``buffer_timeseries.csv`` with headers only.

    Used by the incremental writer so a partially-completed sweep still leaves
    valid, header-bearing files on disk if a later acquisition fails.
    """
    _write_csv(os.path.join(run_dir, METRICS_FILE), METRICS_COLUMNS, [])
    _write_csv(os.path.join(run_dir, TIMESERIES_FILE), TIMESERIES_COLUMNS, [])


def append_metrics(run_dir: str, rows: list[dict]) -> None:
    """Append rows to an existing ``metrics.csv`` (no header)."""
    _append_csv(os.path.join(run_dir, METRICS_FILE), METRICS_COLUMNS, rows)


def append_timeseries(run_dir: str, rows: list[dict]) -> None:
    """Append rows to an existing ``buffer_timeseries.csv`` (no header)."""
    _append_csv(
        os.path.join(run_dir, TIMESERIES_FILE), TIMESERIES_COLUMNS, rows
    )


def write_run_meta(run_dir: str, meta: dict) -> None:
    """Write ``run_meta.json`` (overwrites; safe to call repeatedly)."""
    with open(
        os.path.join(run_dir, RUN_META_FILE), "w", encoding="utf-8"
    ) as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)


def _write_csv(path: str, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def _append_csv(path: str, columns: list[str], rows: list[dict]) -> None:
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def read_csv(path: str) -> list[dict]:
    """Read a CSV into a list of dicts (all values as strings)."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def read_run_meta(run_dir: str) -> dict:
    """Load ``run_meta.json`` from a run directory."""
    with open(
        os.path.join(run_dir, RUN_META_FILE), "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def validate_run_dir(run_dir: str) -> None:
    """Validate that ``run_dir`` conforms to the documented schema.

    Raises
    ------
    ValueError
        If a required file is missing, a CSV header is wrong, or a
        ``run_meta.json`` required key is absent.
    """
    for fname in (RUN_META_FILE, METRICS_FILE, TIMESERIES_FILE):
        fpath = os.path.join(run_dir, fname)
        if not os.path.isfile(fpath):
            raise ValueError(
                "run dir {!r} is missing {!r}".format(run_dir, fname)
            )

    meta = read_run_meta(run_dir)
    missing = [k for k in RUN_META_REQUIRED_KEYS if k not in meta]
    if missing:
        raise ValueError(
            "run_meta.json missing required keys: {}".format(missing)
        )
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "run_meta schema_version {!r} != expected {!r}".format(
                meta.get("schema_version"), SCHEMA_VERSION
            )
        )

    _check_header(os.path.join(run_dir, METRICS_FILE), METRICS_COLUMNS)
    _check_header(os.path.join(run_dir, TIMESERIES_FILE), TIMESERIES_COLUMNS)


def _check_header(path: str, expected: list[str]) -> None:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    if header != expected:
        raise ValueError(
            "{!r} header {} != expected {}".format(path, header, expected)
        )


def load_run_dir(run_dir: str) -> dict:
    """Load a validated run dir into ``{meta, metrics, timeseries}``.

    ``metrics`` rows have numeric fields coerced to ``int`` / ``float`` and
    ``reader`` coerced to ``bool``; ``timeseries`` rows likewise. This is the
    canonical reader used by the analysis step.
    """
    validate_run_dir(run_dir)
    meta = read_run_meta(run_dir)
    metrics = [
        _coerce_metrics_row(r)
        for r in read_csv(os.path.join(run_dir, METRICS_FILE))
    ]
    timeseries = [
        _coerce_timeseries_row(r)
        for r in read_csv(os.path.join(run_dir, TIMESERIES_FILE))
    ]
    return {"meta": meta, "metrics": metrics, "timeseries": timeseries}


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _coerce_metrics_row(row: dict) -> dict:
    out = dict(row)
    out["reader"] = _to_bool(row.get("reader"))
    for key in (
        "batch_size",
        "n_frames",
        "buffer_frames",
        "frame_bytes",
        "frames_produced",
        "frames_written",
        "dropped_count",
    ):
        out[key] = int(float(row[key])) if row.get(key) != "" else 0
    for key in (
        "frame_rate_hz",
        "buffer_mb",
        "dropped_fraction",
        "occupancy_peak",
        "occupancy_mean",
        "throughput_fps",
        "duration_s",
    ):
        out[key] = float(row[key]) if row.get(key) != "" else 0.0
    return out


def _coerce_timeseries_row(row: dict) -> dict:
    out = dict(row)
    out["reader"] = _to_bool(row.get("reader"))
    for key in ("batch_size", "sample_index", "frame_index", "occupancy"):
        out[key] = int(float(row[key])) if row.get(key) != "" else 0
    out["t_rel_s"] = float(row["t_rel_s"]) if row.get("t_rel_s") else 0.0
    return out
