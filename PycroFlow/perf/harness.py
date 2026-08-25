"""The mode-independent measurement loop, sweep driver, and run-dir writer.

Everything here runs identically in ``emulator`` and ``instrument`` mode — the
only thing that changes is the :class:`~PycroFlow.perf.backends.FrameSourceBackend`
returned by :func:`~PycroFlow.perf.backends.make_backend`. For each
configuration the harness:

#. starts the frame source,
#. samples circular-buffer occupancy on a monitor thread (the time series),
#. optionally drives an incremental reader that pulls contiguous batches of
   already-written frames (never subsampled), and
#. on completion, reduces the samples to peak / mean occupancy, dropped
   count / fraction, and write throughput.

:func:`run_sweep` runs the no-reader baseline plus one run per batch size;
:func:`write_run_dir` serialises the results into the documented schema.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

import PycroFlow
from PycroFlow.perf import schema
from PycroFlow.perf.backends import make_backend
from PycroFlow.perf.config import PerfConfig


def run_config(
    cfg: PerfConfig, reader_on: bool, batch_size: int
) -> tuple[dict, list[dict], dict]:
    """Run one configuration and reduce it to a metrics row + time series.

    Parameters
    ----------
    cfg : PerfConfig
        The (already mode-resolved) harness configuration.
    reader_on : bool
        Whether to run the concurrent incremental reader.
    batch_size : int
        Frames per contiguous read when ``reader_on`` (ignored otherwise).

    Returns
    -------
    tuple
        ``(metrics_row, timeseries_rows, backend_describe)``.
    """
    label = "reader_b{}".format(batch_size) if reader_on else "baseline"
    backend = make_backend(cfg, tag=label)
    tag = {
        "mode": cfg.mode,
        "reader": reader_on,
        "batch_size": batch_size if reader_on else 0,
    }
    timeseries: list[dict] = []
    stop_monitor = threading.Event()

    t0 = time.perf_counter()
    backend.start()

    def _monitor() -> None:
        i = 0
        while not stop_monitor.is_set():
            row = dict(tag)
            row["sample_index"] = i
            row["t_rel_s"] = round(time.perf_counter() - t0, 6)
            row["frame_index"] = backend.written()
            row["occupancy"] = backend.occupancy()
            timeseries.append(row)
            i += 1
            time.sleep(cfg.monitor_interval_s)

    def _reader() -> None:
        wait = min(cfg.monitor_interval_s / 2.0, 0.005)
        while True:
            if backend.all_written() and backend.available_for_read() == 0:
                break
            avail = backend.available_for_read()
            if avail >= batch_size:
                backend.read_batch(batch_size)
            elif backend.all_written() and avail > 0:
                backend.read_batch(avail)
            else:
                time.sleep(wait)

    monitor = threading.Thread(
        target=_monitor, name="perf-monitor", daemon=True
    )
    monitor.start()
    reader = None
    if reader_on:
        reader = threading.Thread(
            target=_reader, name="perf-reader", daemon=True
        )
        reader.start()

    while not backend.all_written():
        time.sleep(cfg.monitor_interval_s)
    # Acquisition wall time: everything is produced and written. This is what
    # write throughput is measured against — a reader still draining its
    # backlog afterwards does not slow acquisition and must not count here.
    write_elapsed = time.perf_counter() - t0

    # One final sample so the series always brackets the acquisition.
    final = dict(tag)
    final["sample_index"] = len(timeseries)
    final["t_rel_s"] = round(write_elapsed, 6)
    final["frame_index"] = backend.written()
    final["occupancy"] = backend.occupancy()
    timeseries.append(final)

    stop_monitor.set()
    monitor.join(timeout=5.0)
    if reader is not None:
        reader.join(timeout=60.0)

    describe = backend.describe()
    written = backend.written()
    produced = backend.produced()
    dropped = backend.dropped()
    occ = [r["occupancy"] for r in timeseries] or [0]
    throughput = written / write_elapsed if write_elapsed > 0 else 0.0

    row = dict(tag)
    row.update(
        {
            "n_frames": cfg.n_frames,
            "frame_rate_hz": cfg.frame_rate_hz,
            "buffer_mb": cfg.buffer_mb,
            "buffer_frames": backend.capacity(),
            "frame_bytes": cfg.frame_bytes(),
            "frames_produced": produced,
            "frames_written": written,
            "dropped_count": dropped,
            "dropped_fraction": (
                dropped / cfg.n_frames if cfg.n_frames else 0.0
            ),
            "occupancy_peak": max(occ),
            "occupancy_mean": round(sum(occ) / len(occ), 4),
            "throughput_fps": round(throughput, 4),
            "duration_s": round(write_elapsed, 6),
        }
    )
    backend.close()
    return row, timeseries, describe


def run_sweep(
    cfg: PerfConfig,
) -> tuple[list[dict], list[dict], dict]:
    """Run the baseline (if enabled) plus one run per batch size.

    Returns
    -------
    tuple
        ``(metrics_rows, timeseries_rows, backend_describe)``.
    """
    configs: list[tuple[bool, int]] = []
    if cfg.include_baseline:
        configs.append((False, 0))
    for batch in cfg.batch_sizes:
        configs.append((True, batch))

    metrics: list[dict] = []
    timeseries: list[dict] = []
    describe: dict = {}
    for reader_on, batch in configs:
        row, ts, desc = run_config(cfg, reader_on, batch)
        metrics.append(row)
        timeseries.extend(ts)
        describe = desc or describe
    return metrics, timeseries, describe


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _pycromanager_version() -> str:
    try:
        import pycromanager

        return getattr(pycromanager, "__version__", "unknown")
    except Exception:
        return "not-installed"


def build_run_meta(
    cfg: PerfConfig,
    describe: dict,
    utc_start: str,
    utc_end: str,
) -> dict:
    """Assemble the ``run_meta.json`` provenance record."""
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "mode": cfg.mode,
        "host": socket.gethostname(),
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "pycroflow_version": getattr(PycroFlow, "__version__", "unknown"),
        "pycromanager_version": _pycromanager_version(),
        "frame_rate_hz": cfg.frame_rate_hz,
        "n_frames": cfg.n_frames,
        "buffer_mb": cfg.buffer_mb,
        "buffer_frames": cfg.buffer_capacity_frames(),
        "frame_bytes": cfg.frame_bytes(),
        "batch_sizes": list(cfg.batch_sizes),
        "exposure_ms": cfg.exposure_ms,
        "roi": cfg.roi,
        "data_dir": cfg.data_dir,
        "monitor_interval_s": cfg.monitor_interval_s,
        "git_commit": _git_commit(),
        "utc_start": utc_start,
        "utc_end": utc_end,
        "config": cfg.to_dict(),
        "backend": describe,
        "thresholds": schema.DEFAULT_THRESHOLDS,
    }


def _timestamp_for_dirname() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_and_write(cfg: PerfConfig) -> str:
    """Run the full sweep and write a timestamped run dir; return its path."""
    utc_start = _utc_iso()
    metrics, timeseries, describe = run_sweep(cfg)
    utc_end = _utc_iso()
    return write_run_dir(
        cfg, metrics, timeseries, describe, utc_start, utc_end
    )


def write_run_dir(
    cfg: PerfConfig,
    metrics: list[dict],
    timeseries: list[dict],
    describe: dict,
    utc_start: str,
    utc_end: str,
) -> str:
    """Write ``run_meta.json`` / ``metrics.csv`` / ``buffer_timeseries.csv``.

    Returns
    -------
    str
        The created run-directory path.
    """
    dirname = "{}_{}_{}".format(cfg.label, cfg.mode, _timestamp_for_dirname())
    run_dir = os.path.join(cfg.output_dir, dirname)
    os.makedirs(run_dir, exist_ok=True)

    meta = build_run_meta(cfg, describe, utc_start, utc_end)
    with open(
        os.path.join(run_dir, schema.RUN_META_FILE),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    schema.write_metrics(os.path.join(run_dir, schema.METRICS_FILE), metrics)
    schema.write_timeseries(
        os.path.join(run_dir, schema.TIMESERIES_FILE), timeseries
    )
    return run_dir
