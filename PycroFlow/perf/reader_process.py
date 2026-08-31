"""Separate-process incremental NDTiff reader for the WP-1 harness.

This is the design-intended live reader: a **separate OS process** that opens
the movie Micro-Manager is *still writing* and reads the already-written frames
in contiguous batches, trailing behind acquisition. Because it is a distinct
process reading files off disk (not a thread sharing the acquisition's GIL /
pycromanager ZMQ bridge), it isolates whether concurrent reading *fundamentally*
contends with the write path — the open question the same-process (thread)
reader left.

By default it reads through **picasso** (``picasso.io.TiffMultiMap``) — the
lab's analysis package and its actual reader for Micro-Manager NDTiff / OME-TIFF
movies. picasso/tifffile build a per-frame byte-offset table by walking the
TIFF IFDs once, then read each frame as a pure ``seek`` + ``readinto`` from its
offset, and drop a partially-written trailing IFD — so it reads a still-growing
file efficiently and safely, and generates exactly the read-load a real live
analysis would. If picasso is not installed (or fails), it falls back to reading
via ndtiff's ``Dataset``.

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
import re
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


def _import_dataset():  # pragma: no cover - depends on the installed SDK
    """Return the NDTiff ``Dataset`` class across pycromanager versions.

    The reader-facing NDTiff reader has moved as the stack evolved: it used to
    be re-exported at the top level of ``pycromanager`` (``from pycromanager
    import Dataset``), then lived in the ``ndtiff`` package, and in current
    releases in ``ndstorage`` (which pycromanager's own ``get_dataset`` returns).
    Try them in turn so the separate reader process works regardless of which
    versions are installed on the acquisition PC, and raise a clear error
    listing what was tried if none resolve.
    """
    candidates = (
        ("ndstorage", "Dataset"),
        ("ndstorage", "NDTiffDataset"),
        ("ndtiff", "Dataset"),
        ("ndtiff", "NDTiffDataset"),
        ("pycromanager", "Dataset"),
    )
    tried = []
    for mod_name, attr in candidates:
        try:
            mod = __import__(mod_name, fromlist=[attr])
            return getattr(mod, attr)
        except (ImportError, AttributeError) as exc:
            tried.append("{}.{}: {}".format(mod_name, attr, exc))
    raise ImportError(
        "could not import an NDTiff Dataset class from any of "
        "ndstorage / ndtiff / pycromanager; tried:\n  " + "\n  ".join(tried)
    )


def _write_count(count_file: str | None, count: int) -> None:
    if not count_file:
        return
    try:
        with open(count_file, "w", encoding="utf-8") as fh:
            fh.write(str(count))
    except OSError:
        pass


def _axis_layout(dataset) -> tuple[str | None, dict]:
    """Pick the frame (time) axis and hold the other axes at their first value.

    pycromanager's ``multi_d_acquisition_events(num_time_points=N)`` names the
    frame axis ``"time"``, not ``"t"``, and newer ndtiff raises ``KeyError`` if
    you query an axis name it doesn't know. So rather than hardcode ``t=``, read
    the dataset's own axis names: use the one that looks like the time axis (by
    name, else the one with the most positions) as the frame axis, and pin every
    other axis (channel / z / position, typically singletons here) to its
    minimum so a coordinate fully specifies one frame.

    Returns
    -------
    tuple
        ``(frame_axis_name_or_None, fixed_coords)``.
    """
    axes = dict(getattr(dataset, "axes", {}) or {})
    frame_axis = None
    for cand in ("time", "t", "frame", "timepoint"):
        for name in axes:
            if name.lower() == cand:
                frame_axis = name
                break
        if frame_axis is not None:
            break
    if frame_axis is None and axes:
        frame_axis = max(axes, key=lambda a: len(axes[a]))
    fixed: dict = {}
    for name, positions in axes.items():
        if name == frame_axis:
            continue
        try:
            fixed[name] = min(positions)
        except (TypeError, ValueError):
            pass
    return frame_axis, fixed


def _index_signature(ds_dir: str) -> tuple:
    """Cheap fingerprint of the dataset's on-disk size, to detect growth.

    Only ``stat``\\ s the NDTiff index + TIFF files (never reads them), so the
    reader can tell whether new frames have been written since its last open
    without paying for a re-open. Returns a hashable tuple.
    """
    sig = []
    for pattern in ("*NDTiff.index", "*.tif", "*.tiff"):
        for path in sorted(glob.glob(os.path.join(ds_dir, pattern))):
            try:
                sig.append((os.path.basename(path), os.stat(path).st_size))
            except OSError:
                continue
    return tuple(sig)


def _await_dataset_dir(
    acq_dir: str, stop_file: str, poll_s: float
) -> str | None:  # pragma: no cover - timing against a live acquisition
    """Wait for the acquisition's dataset subdir to appear.

    Returns the dataset directory, or ``None`` if the stop-file appears first.
    """
    while True:
        if os.path.exists(stop_file):
            return None
        ds_dir = _find_dataset_dir(acq_dir)
        if ds_dir is not None:
            return ds_dir
        time.sleep(poll_s)


def _find_movie_file(ds_dir: str) -> str | None:
    """Return the base data file to hand to picasso, or None.

    picasso's :class:`~picasso.io.TiffMultiMap` opens a Micro-Manager movie
    from its *first* file and globs the numbered continuations itself. Prefer
    the NDTiff stack (pycromanager's default: ``*_NDTiffStack.tif``), then a
    Micro-Manager OME-TIFF (``*.ome.tif``), then any ``*.tif`` — in each case
    the *base* file, i.e. the one without a ``_<n>`` continuation suffix.
    """
    nd = glob.glob(os.path.join(ds_dir, "*NDTiffStack*.tif"))
    base = [p for p in nd if not re.search(r"_\d+\.tif$", os.path.basename(p))]
    if base:
        return sorted(base)[0]
    if nd:
        return sorted(nd)[0]
    ome = glob.glob(os.path.join(ds_dir, "*.ome.tif"))
    obase = [
        p
        for p in ome
        if not re.search(r"_\d+\.ome\.tif$", os.path.basename(p))
    ]
    if obase:
        return sorted(obase)[0]
    if ome:
        return sorted(ome)[0]
    anytif = sorted(glob.glob(os.path.join(ds_dir, "*.tif")))
    return anytif[0] if anytif else None


def _import_picasso_movie():  # pragma: no cover - depends on installed picasso
    """Return picasso's ``TiffMultiMap`` class, or None if unavailable.

    picasso is the lab's analysis package and its ``TiffMap`` / ``TiffMultiMap``
    are the *actual* readers it uses on Micro-Manager NDTiff / OME-TIFF movies:
    tifffile builds a per-frame byte-offset table (walking the TIFF IFDs once)
    and each frame is a pure ``seek`` + ``readinto`` from its offset — the
    efficient "remember where each frame is" read the harness wants, and the
    read-load a real live analysis would produce. It also drops a
    partially-written trailing IFD, so reading a still-growing file is safe.
    """
    try:
        from picasso.io import TiffMultiMap

        return TiffMultiMap
    except Exception:
        return None


def _read_loop_picasso(
    TiffMultiMap,
    ds_dir: str,
    batch_size: int,
    stop_file: str,
    count_file: str | None,
    poll_s: float,
    reopen_interval_s: float,
) -> int:  # pragma: no cover - needs a real movie on the acquisition PC
    """Read the growing movie via picasso, in contiguous batches.

    picasso captures the frames present at open time, so — as with any
    off-disk reader of a live file — new frames are picked up by re-opening.
    But picasso's re-open is a fast tifffile IFD/offset scan (not ndtiff's
    index rebuild), so re-opening is cheap; it is still throttled to
    ``reopen_interval_s`` and only done when the files have grown. On stop the
    movie is finalised, so a last re-open sees every frame.
    """
    print("[wp1-reader] using picasso TiffMultiMap reader", flush=True)
    read = 0
    movie = None
    n_avail = 0
    last_open = 0.0
    last_sig: tuple | None = None
    try:
        while True:
            stopping = os.path.exists(stop_file)
            movie_file = _find_movie_file(ds_dir)
            if movie_file is None:
                if stopping:
                    break
                time.sleep(poll_s)
                continue

            need_open = movie is None or stopping
            if (
                not need_open
                and (time.monotonic() - last_open) >= reopen_interval_s
            ):
                if _index_signature(ds_dir) != last_sig:
                    need_open = True
            if need_open:
                t_open = time.monotonic()
                try:
                    fresh = TiffMultiMap(movie_file)
                except Exception:
                    # Still being written (partial trailing IFD) — retry later.
                    if stopping:
                        break
                    time.sleep(poll_s)
                    continue
                if movie is not None:
                    try:
                        movie.close()
                    except Exception:
                        pass
                movie = fresh
                n_avail = movie.n_frames
                last_open = time.monotonic()
                last_sig = _index_signature(ds_dir)
                print(
                    "[wp1-reader] opened movie in {:.1f}s "
                    "({} frames available, {} read)".format(
                        last_open - t_open, n_avail, read
                    ),
                    flush=True,
                )

            progressed = False
            while (n_avail - read) >= batch_size or (
                stopping and n_avail > read
            ):
                take = min(batch_size, n_avail - read)
                for i in range(read, read + take):
                    movie.get_frame(i)
                read += take
                progressed = True
                _write_count(count_file, read)

            if stopping and read >= n_avail:
                break
            if not progressed:
                time.sleep(poll_s)
    finally:
        if movie is not None:
            try:
                movie.close()
            except Exception:
                pass
    _write_count(count_file, read)
    return read


def _read_loop_reopen(
    ds_dir: str,
    batch_size: int,
    stop_file: str,
    count_file: str | None,
    poll_s: float,
    reopen_interval_s: float,
) -> int:  # pragma: no cover - needs a real NDTiff on the acquisition PC
    """Fallback reader via ndtiff's ``Dataset`` (used when picasso is absent).

    Opens the dataset once and re-opens only rarely (throttled + on index
    growth): re-opening a still-being-written NDTiff makes ndtiff rebuild its
    index by scanning the TIFF IFDs (O(dataset size)), so it must be minimised.
    """
    Dataset = _import_dataset()
    print(
        "[wp1-reader] using {}.{}".format(
            Dataset.__module__, Dataset.__name__
        ),
        flush=True,
    )

    read = 0
    dataset = None
    frame_axis: str | None = None
    fixed: dict = {}
    last_open = 0.0
    last_sig: tuple | None = None

    def _coords(i: int) -> dict:
        coords = dict(fixed)
        coords[frame_axis if frame_axis is not None else "t"] = i
        return coords

    while True:
        stopping = os.path.exists(stop_file)

        need_open = dataset is None or stopping
        if (
            not need_open
            and (time.monotonic() - last_open) >= reopen_interval_s
        ):
            if _index_signature(ds_dir) != last_sig:
                need_open = True
        if need_open:
            t_open = time.monotonic()
            try:
                dataset = Dataset(ds_dir)
            except Exception:
                if stopping:
                    break
                time.sleep(poll_s)
                continue
            frame_axis, fixed = _axis_layout(dataset)
            last_open = time.monotonic()
            last_sig = _index_signature(ds_dir)
            print(
                "[wp1-reader] opened dataset in {:.1f}s "
                "(frames read so far: {})".format(last_open - t_open, read),
                flush=True,
            )

        progressed = False
        while True:
            if not dataset.has_image(**_coords(read)):
                break
            if not stopping and not dataset.has_image(
                **_coords(read + batch_size - 1)
            ):
                break
            end = read + batch_size
            idx = read
            while idx < end and dataset.has_image(**_coords(idx)):
                try:
                    dataset.read_image(**_coords(idx))
                except Exception:
                    break
                idx += 1
            if idx == read:
                break
            read = idx
            progressed = True
            _write_count(count_file, read)

        if stopping:
            break
        if not progressed:
            time.sleep(poll_s)

    _write_count(count_file, read)
    return read


def _read_loop(
    acq_dir: str,
    batch_size: int,
    stop_file: str,
    count_file: str | None,
    poll_s: float,
    reopen_interval_s: float,
) -> int:  # pragma: no cover - dispatches to a reader that needs a real movie
    """Read the growing movie in batches until ``stop_file`` appears.

    Prefers picasso's reader (the analysis package's own, most representative
    of a real live reader); falls back to ndtiff's ``Dataset`` if picasso is
    not installed or fails. Returns the total frames read.
    """
    ds_dir = _await_dataset_dir(acq_dir, stop_file, poll_s)
    if ds_dir is None:
        _write_count(count_file, 0)
        return 0

    TiffMultiMap = _import_picasso_movie()
    if TiffMultiMap is not None:
        try:
            return _read_loop_picasso(
                TiffMultiMap,
                ds_dir,
                batch_size,
                stop_file,
                count_file,
                poll_s,
                reopen_interval_s,
            )
        except Exception as exc:  # noqa: BLE001 - degrade to the ndtiff reader
            print(
                "[wp1-reader] picasso reader failed ({!r}); falling back to "
                "the ndtiff re-open reader".format(exc),
                flush=True,
            )
    else:
        print(
            "[wp1-reader] picasso not importable; using ndtiff re-open reader",
            flush=True,
        )
    return _read_loop_reopen(
        ds_dir,
        batch_size,
        stop_file,
        count_file,
        poll_s,
        reopen_interval_s,
    )


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
    parser.add_argument(
        "--reopen-interval",
        type=float,
        default=2.0,
        help=(
            "Minimum seconds between re-opens of the growing dataset "
            "(re-opening rescans the TIFF IFDs, so keep it infrequent)."
        ),
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
        args.reopen_interval,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
