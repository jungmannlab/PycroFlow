"""Separate-process incremental NDTiff reader for the WP-1 harness.

This is the design-intended live reader: a **separate OS process** that opens
the NDTiff dataset Micro-Manager is *still writing* and reads the already-
written frames in contiguous batches, trailing behind acquisition. Because it
is a distinct process reading files off disk (not a thread sharing the
acquisition's GIL / pycromanager ZMQ bridge), it isolates whether concurrent
reading *fundamentally* contends with the write path — the open question the
same-process (thread) reader left.

It is launched by :class:`~PycroFlow.perf.backends.InstrumentBackend` as::

    python -m PycroFlow.perf.reader_process --acq-dir <dir> --batch <n> \
        --stop-file <path> --count-file <path> [--poll <s>]

and stopped by the parent creating ``--stop-file``. On exit it writes the total
number of frames it read to ``--count-file`` (so the harness can confirm the
reader actually kept up and read every frame — never subsampled).

pycromanager is imported lazily inside the read loop so this module imports on
dev / CI (where the SDK is absent). The read loop itself only runs on the
acquisition PC and is therefore not covered by the hermetic test suite; the
argument handling and the already-stopped fast path are.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time


def _find_dataset_dir(acq_dir: str) -> str | None:
    """Return the NDTiff dataset subdirectory under ``acq_dir``, or None.

    pycromanager writes the acquisition into a subfolder (e.g. ``acq_1``)
    holding the NDTiff ``*.tif`` files and a ``*NDTiff.index``. Pick the first
    subdir that looks like a dataset.
    """
    for sub in sorted(glob.glob(os.path.join(acq_dir, "*"))):
        if not os.path.isdir(sub):
            continue
        if glob.glob(os.path.join(sub, "*NDTiff.index")) or glob.glob(
            os.path.join(sub, "*.tif")
        ):
            return sub
    return None


def _write_count(count_file: str | None, count: int) -> None:
    if not count_file:
        return
    try:
        with open(count_file, "w", encoding="utf-8") as fh:
            fh.write(str(count))
    except OSError:
        pass


def _read_loop(
    acq_dir: str,
    batch_size: int,
    stop_file: str,
    count_file: str | None,
    poll_s: float,
) -> int:  # pragma: no cover - needs a real NDTiff on the acquisition PC
    """Read the growing NDTiff in batches until ``stop_file`` appears.

    Returns the total number of frames read.
    """
    from pycromanager import Dataset

    # Wait for the dataset to appear (acquisition creates it on the first
    # frame), unless we are asked to stop first.
    ds_dir = None
    while ds_dir is None:
        if os.path.exists(stop_file):
            _write_count(count_file, 0)
            return 0
        ds_dir = _find_dataset_dir(acq_dir)
        if ds_dir is None:
            time.sleep(poll_s)

    read = 0
    while True:
        stopping = os.path.exists(stop_file)
        try:
            dataset = Dataset(ds_dir)
        except Exception:
            if stopping:
                break
            time.sleep(poll_s)
            continue

        progressed = False
        while True:
            # Normally wait for a full contiguous batch to be available; when
            # stopping, flush whatever remains (still contiguous, never
            # subsampled).
            if not dataset.has_image(t=read):
                break
            if not stopping and not dataset.has_image(t=read + batch_size - 1):
                break
            end = read + batch_size
            idx = read
            while idx < end and dataset.has_image(t=idx):
                try:
                    dataset.read_image(t=idx)
                except Exception:
                    break
                idx += 1
            if idx == read:
                break
            read = idx
            progressed = True
            _write_count(count_file, read)

        if stopping and not progressed:
            break
        time.sleep(poll_s)

    _write_count(count_file, read)
    return read


def build_parser() -> argparse.ArgumentParser:
    """Build the reader-process argument parser."""
    parser = argparse.ArgumentParser(
        prog="pycroflow-perf-reader",
        description=(
            "Separate-process incremental NDTiff reader for the WP-1 "
            "performance harness (launched by the harness, not run by hand)."
        ),
    )
    parser.add_argument(
        "--acq-dir",
        required=True,
        help="Directory the acquisition writes the NDTiff dataset into.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        required=True,
        help="Contiguous frames to read per batch.",
    )
    parser.add_argument(
        "--stop-file",
        required=True,
        help="The reader stops once this file exists.",
    )
    parser.add_argument(
        "--count-file",
        default=None,
        help="Total frames read is written here on exit.",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=0.05,
        help="Poll interval (s) while waiting for new frames.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: read until the stop-file appears, then report the count."""
    args = build_parser().parse_args(argv)
    # Fast path: if we are asked to stop before doing anything, exit cleanly
    # without importing the SDK (keeps this unit-testable off-instrument).
    if os.path.exists(args.stop_file):
        _write_count(args.count_file, 0)
        return 0
    _read_loop(
        args.acq_dir,
        args.batch,
        args.stop_file,
        args.count_file,
        args.poll,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
