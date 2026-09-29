"""Frame-source abstraction for live analysis — backends behind one interface.

The pipeline reads frames from *somewhere* and only that "somewhere" differs by
mode, so it is confined here behind :class:`FrameSource`, which yields
:class:`Batch` objects (a contiguous run of frames from one stage position, with
the absolute index of the first frame). Backends:

* :class:`TiffTailFrameSource` — **the default, lossless** source. Micro-Manager
  writes every frame to a growing movie on local disk; this tails it, reading
  newly-finished frames in contiguous batches, trailing behind acquisition. It
  reuses WP-1's reader (picasso ``io.TiffMultiMap`` — the lab's own reader,
  which builds a per-frame byte-offset table and reads each frame as a pure
  ``seek`` + ``readinto``, and drops a partially-written trailing IFD so a
  still-growing file is read safely). Because frames wait on disk, nothing is
  lost when the consumer is busy. It is designed to run in a **separate OS
  process** from acquisition (per WP-1: a same-process reader contends); the
  service launches it as such (see :mod:`compute_backend`), and this class is
  also directly iterable in-process for tests. Handles MM's ~4 GB rollover
  (``_1``, ``_2`` … via natural sort) and per-``_Pos<N>`` batching (a batch never
  spans two stage positions).

* :class:`RamPeekFrameSource` — a **lossy** RAM circular-buffer PEEK via
  pycromanager ``Core.get_last_tagged_image`` (non-destructive: it does not
  steal frames from MM's own save pipeline). For the live view only — it may
  skip frames under load, so it is NEVER the authoritative reduction source.

* :class:`MockFrameSource` — a synthetic in-memory stream for hermetic tests.

pycromanager / picasso / tifffile are imported lazily inside the backends that
need them, so this module imports on dev / CI (where those SDKs are absent).
"""

from __future__ import annotations

import abc
import glob
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

# ── filename parsing (shared with the lab's Live Localization tool) ─────────
# MM's MDA saver names every file of one stage position with the same _Pos<N>
# token, so the position a frame belongs to is recoverable from the filename
# alone — no hardware metadata needed.
_POS_RE = re.compile(r"_Pos(\d+)", re.IGNORECASE)


def position_from_name(path: str | None) -> int | None:
    """Return the MM stage-position index in a filename, or None if absent.

    ``None`` for a single-position / non-MDA acquisition (no ``_Pos<N>`` token).
    """
    if not path:
        return None
    m = _POS_RE.search(os.path.basename(str(path)))
    return int(m.group(1)) if m else None


def natural_key(path: str) -> list:
    """Natural-sort key so MM rollover files stay in order: ``_9`` before ``_10``.

    MM splits a movie at ~4 GB into ``_1``, ``_2`` … . Lexicographic order sorts
    ``_10`` before ``_2`` and stalls the reader after ~10 files; splitting on
    digit runs and comparing the numbers as ints fixes that.
    """
    name = os.path.basename(path)
    return [
        int(tok) if tok.isdigit() else tok.lower()
        for tok in re.split(r"(\d+)", name)
    ]


@dataclass
class Batch:
    """A contiguous run of frames from one stage position.

    Attributes
    ----------
    frames : np.ndarray
        ``(n, height, width)`` stack, dtype as read from the movie.
    start_frame : int
        Absolute index of the first frame — passed straight to
        ``localize_frames(..., start_frame=start_frame)`` so localization tables
        concatenate with contiguous, absolute frame indices across batches.
    position : int | None
        MM ``_Pos<N>`` index (None for single-position / peek / mock streams). A
        batch never spans two positions.
    """

    frames: np.ndarray
    start_frame: int
    position: int | None = None

    @property
    def n_frames(self) -> int:
        return int(self.frames.shape[0])


class FrameSource(abc.ABC):
    """Interface the pipeline drives: iterate contiguous, position-pure batches.

    A source is started, then :meth:`batches` is iterated until the source is
    :meth:`close`\\ d (or the acquisition ends). Concrete sources decide whether
    they are lossless (tail) or lossy (peek); the authoritative reduction path
    must only ever use a lossless one.
    """

    #: True for lossless sources safe to use as the authoritative stream.
    lossless: bool = True

    @abc.abstractmethod
    def batches(self, batch_size: int) -> Iterator[Batch]:
        """Yield :class:`Batch` objects until the source is closed/exhausted.

        Each batch holds up to ``batch_size`` contiguous frames from a single
        stage position. Never subsamples: for a lossless source every produced
        frame is emitted in exactly one batch.
        """

    @abc.abstractmethod
    def frames_read(self) -> int:
        """Total frames emitted so far (the authoritative-coverage counter)."""

    @abc.abstractmethod
    def camera_info(self) -> dict | None:
        """Picasso ``info``/camera dict for the movie, or None if unknown yet."""

    def close(self) -> None:
        """Stop producing and release resources (idempotent)."""


# ── TIFF-tail: the default lossless source ──────────────────────────────────


class TiffTailFrameSource(FrameSource):
    """Tail a growing Micro-Manager movie on local disk (lossless, DEFAULT).

    Reads newly-finished frames in contiguous batches via picasso's
    ``TiffMultiMap`` (WP-1's reader). ``TiffMultiMap`` opens a movie from its
    first file and globs the numbered continuations itself, so rollover is
    handled by the reader; we additionally natural-sort our own file discovery
    and honour per-position batching. Because ``TiffMultiMap`` captures the
    frames present at open time, new frames are picked up by a cheap re-open
    (a tifffile IFD/offset rescan), throttled to ``reopen_interval_s`` and only
    when the on-disk files have grown.

    Parameters
    ----------
    acq_dir : str
        Directory MM writes the movie into (scanned for the movie file).
    poll_s : float
        Poll interval while waiting for new frames / the movie to appear.
    reopen_interval_s : float
        Minimum seconds between re-opens of the growing movie.
    stop_event : threading.Event | None
        External stop signal; when set (and no frames remain) the iterator ends.
        One is created if not supplied.
    """

    lossless = True

    def __init__(
        self,
        acq_dir: str,
        *,
        poll_s: float = 0.2,
        reopen_interval_s: float = 2.0,
        stop_event: threading.Event | None = None,
    ):
        self.acq_dir = acq_dir
        self.poll_s = poll_s
        self.reopen_interval_s = reopen_interval_s
        self._stop = stop_event or threading.Event()
        self._read = 0
        self._info: dict | None = None
        self._movie = None

    # -- file discovery (natural-sorted, rollover-aware) ---------------------
    def _find_movie_file(self) -> str | None:
        """Return the *base* movie file to hand to picasso, or None.

        picasso globs the ``_<n>`` continuations itself, so we return the base
        (no ``_<n>`` suffix), preferring NDTiff then OME-TIFF then any ``.tif``.
        Directories are scanned recursively (MM nests the dataset in a subdir).
        """
        found: list[str] = []
        for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
            found += glob.glob(os.path.join(self.acq_dir, pat))
            found += glob.glob(
                os.path.join(self.acq_dir, "**", pat), recursive=True
            )
        tifs = sorted(set(found), key=natural_key)
        if not tifs:
            return None

        def _base(cands: list[str], suffix_re: str) -> list[str]:
            return [
                p
                for p in cands
                if not re.search(suffix_re, os.path.basename(p))
            ]

        nd = [p for p in tifs if "NDTiffStack" in os.path.basename(p)]
        base_nd = _base(nd, r"_\d+\.tif$")
        if base_nd:
            return base_nd[0]
        if nd:
            return nd[0]
        ome = [p for p in tifs if p.lower().endswith(".ome.tif")]
        base_ome = _base(ome, r"_\d+\.ome\.tif$")
        if base_ome:
            return base_ome[0]
        if ome:
            return ome[0]
        return tifs[0]

    def _index_signature(self) -> tuple:
        """Cheap on-disk size fingerprint to detect growth without re-reading."""
        sig = []
        for pat in ("*.tif", "*.tiff", "*NDTiff.index"):
            for p in sorted(
                glob.glob(
                    os.path.join(self.acq_dir, "**", pat), recursive=True
                )
            ):
                try:
                    sig.append((os.path.basename(p), os.stat(p).st_size))
                except OSError:
                    continue
        return tuple(sig)

    def _open_movie(self, movie_file: str):
        from picasso.io import TiffMultiMap

        movie = TiffMultiMap(movie_file)
        if self._info is None:
            self._info = self._extract_info(movie)
        return movie

    @staticmethod
    def _extract_info(movie) -> dict | None:
        info = getattr(movie, "info", None)
        if info:
            # picasso movies expose info as a list-of-dicts; keep the first.
            return info[0] if isinstance(info, (list, tuple)) else info
        return None

    def camera_info(self) -> dict | None:
        return self._info

    def frames_read(self) -> int:
        return self._read

    def close(self) -> None:
        self._stop.set()
        if self._movie is not None:
            try:
                self._movie.close()
            except Exception:
                pass
            self._movie = None

    def batches(self, batch_size: int) -> Iterator[Batch]:
        movie = None
        n_avail = 0
        last_open = 0.0
        last_sig: tuple | None = None
        movie_pos: int | None = None
        try:
            while True:
                stopping = self._stop.is_set()
                movie_file = self._find_movie_file()
                if movie_file is None:
                    if stopping:
                        break
                    time.sleep(self.poll_s)
                    continue
                movie_pos = position_from_name(movie_file)

                need_open = movie is None or stopping
                if (
                    not need_open
                    and (time.monotonic() - last_open)
                    >= self.reopen_interval_s
                ):
                    if self._index_signature() != last_sig:
                        need_open = True
                if need_open:
                    try:
                        fresh = self._open_movie(movie_file)
                    except Exception:
                        # Partial trailing IFD — retry next cycle.
                        if stopping:
                            break
                        time.sleep(self.poll_s)
                        continue
                    if movie is not None:
                        try:
                            movie.close()
                        except Exception:
                            pass
                    movie = fresh
                    self._movie = movie
                    n_avail = movie.n_frames
                    last_open = time.monotonic()
                    last_sig = self._index_signature()

                progressed = False
                while (n_avail - self._read) >= batch_size or (
                    stopping and n_avail > self._read
                ):
                    take = min(batch_size, n_avail - self._read)
                    frames = np.stack(
                        [
                            np.asarray(movie.get_frame(i))
                            for i in range(self._read, self._read + take)
                        ]
                    )
                    yield Batch(
                        frames=frames,
                        start_frame=self._read,
                        position=movie_pos,
                    )
                    self._read += take
                    progressed = True

                if stopping and self._read >= n_avail:
                    break
                if not progressed:
                    time.sleep(self.poll_s)
        finally:
            self.close()


# ── RAM peek: lossy live-view source ────────────────────────────────────────


@dataclass
class _PeekFrame:
    index: int
    array: np.ndarray
    position: int | None = None
    t_recv: float = field(default_factory=time.time)


class RamPeekFrameSource(FrameSource):
    """Non-destructive live peek at a running MM circular buffer (LOSSY).

    Peeks the newest frame with ``Core.get_last_tagged_image`` (which does not
    remove it from MM's save pipeline), de-duplicates by ``ImageNumber``, and
    counts gaps. For the **live view only** — it may skip frames under load, so
    it is not lossless and MUST NOT drive the authoritative reduction. Runs a
    poller thread; :meth:`batches` drains the internal queue.

    Parameters
    ----------
    poll_s : float
        Seconds between peeks (well below the frame period).
    port : int
        MM ZMQ bridge port.
    maxsize : int
        Internal queue cap; when full the oldest frame is dropped (fresh-first).
    """

    lossless = False

    def __init__(
        self, *, poll_s: float = 0.02, port: int = 4827, maxsize: int = 2000
    ):
        self.poll_s = poll_s
        self.port = port
        self._q: "queue.Queue[_PeekFrame]" = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._core = None
        self._last_index: int | None = None
        self._read = 0
        self.n_missed = 0
        self.n_dropped = 0

    def start(self) -> "RamPeekFrameSource":
        self._thread = threading.Thread(
            target=self._produce, name="live-rampeek", daemon=True
        )
        self._thread.start()
        return self

    def camera_info(self) -> dict | None:
        return None

    def frames_read(self) -> int:
        return self._read

    def _produce(self) -> None:  # pragma: no cover - needs a running MM
        try:
            from pycromanager import Core

            self._core = Core(port=self.port)
        except Exception:
            self._stop.set()
            return
        while not self._stop.is_set():
            try:
                timg = self._core.get_last_tagged_image()
            except Exception:
                time.sleep(self.poll_s)
                continue
            tags = dict(timg.tags)
            idx = self._extract_index(tags)
            if idx is None or idx == self._last_index:
                time.sleep(self.poll_s)
                continue
            hw = self._hw(tags)
            if hw is None:
                time.sleep(self.poll_s)
                continue
            if self._last_index is not None and idx > self._last_index + 1:
                self.n_missed += idx - self._last_index - 1
            self._last_index = idx
            h, w = hw
            arr = np.reshape(np.asarray(timg.pix), (h, w))
            self._offer(_PeekFrame(idx, arr, tags.get("PositionIndex")))
            time.sleep(self.poll_s)

    def _offer(self, fr: _PeekFrame) -> None:
        try:
            self._q.put_nowait(fr)
        except queue.Full:
            try:
                self._q.get_nowait()
                self.n_dropped += 1
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(fr)
            except queue.Full:
                self.n_dropped += 1

    @staticmethod
    def _extract_index(tags: dict) -> int | None:
        for k in ("ImageNumber", "Frame", "FrameIndex"):
            if k in tags:
                try:
                    return int(tags[k])
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _hw(tags: dict) -> tuple[int, int] | None:
        h = w = None
        for k, v in tags.items():
            if k == "Height" or k.endswith("-Height"):
                h = int(v)
            elif k == "Width" or k.endswith("-Width"):
                w = int(v)
        return (h, w) if h and w else None

    def batches(self, batch_size: int) -> Iterator[Batch]:
        buf: list[_PeekFrame] = []
        while not self._stop.is_set():
            try:
                fr = self._q.get(timeout=self.poll_s * 5 or 0.1)
            except queue.Empty:
                if buf:
                    yield self._flush(buf)
                    buf = []
                continue
            if buf and fr.position != buf[0].position:
                yield self._flush(buf)
                buf = []
            buf.append(fr)
            if len(buf) >= batch_size:
                yield self._flush(buf)
                buf = []
        if buf:
            yield self._flush(buf)

    def _flush(self, buf: list[_PeekFrame]) -> Batch:
        frames = np.ascontiguousarray(np.stack([f.array for f in buf]))
        self._read += len(buf)
        return Batch(
            frames=frames, start_frame=buf[0].index, position=buf[0].position
        )

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


# ── Mock: hermetic tests ────────────────────────────────────────────────────


class MockFrameSource(FrameSource):
    """Synthetic in-memory frame stream for hermetic tests.

    Produces ``n_frames`` sparse-blinking frames (a Poisson background with a
    handful of Gaussian spots), optionally split across stage positions so the
    per-position batching path is exercised without a microscope. Lossless: it
    is authoritative for the tests that assert full coverage.

    Parameters
    ----------
    n_frames : int
        Total frames to produce.
    height, width : int
        Frame dimensions.
    positions : int
        Stage positions to spread the frames over (>=1). A batch never spans two.
    produce_delay_s : float
        Optional per-frame delay so a test can model a producer running at rate.
    seed : int
        RNG seed for reproducible frames.
    """

    lossless = True

    def __init__(
        self,
        n_frames: int = 200,
        *,
        height: int = 48,
        width: int = 48,
        positions: int = 1,
        produce_delay_s: float = 0.0,
        seed: int = 0,
    ):
        if n_frames <= 0:
            raise ValueError("n_frames must be > 0")
        if positions < 1:
            raise ValueError("positions must be >= 1")
        self.n_frames = n_frames
        self.height = height
        self.width = width
        self.positions = positions
        self.produce_delay_s = produce_delay_s
        self._rng = np.random.default_rng(seed)
        self._read = 0
        self._stop = threading.Event()
        # Deterministic camera info so localize_frames has what it needs.
        self._info = {
            "Baseline": 100,
            "Sensitivity": 1.0,
            "Gain": 1,
            "Qe": 0.9,
            "Pixelsize": 130,
            "Height": height,
            "Width": width,
            "Frames": n_frames,
        }

    def camera_info(self) -> dict | None:
        return self._info

    def frames_read(self) -> int:
        return self._read

    def close(self) -> None:
        self._stop.set()

    def _make_frame(self) -> np.ndarray:
        img = self._rng.poisson(lam=100, size=(self.height, self.width))
        img = img.astype(np.uint16)
        n = int(self._rng.poisson(4))
        for _ in range(n):
            cy = int(self._rng.integers(4, self.height - 4))
            cx = int(self._rng.integers(4, self.width - 4))
            amp = int(self._rng.integers(400, 3000))
            yy, xx = np.mgrid[cy - 3 : cy + 4, cx - 3 : cx + 4]
            g = amp * np.exp(
                -(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.1**2))
            )
            img[cy - 3 : cy + 4, cx - 3 : cx + 4] += g.astype(np.uint16)
        return img

    def _position_of(self, frame_idx: int) -> int | None:
        if self.positions == 1:
            return None
        per = self.n_frames // self.positions
        per = max(1, per)
        return min(self.positions - 1, frame_idx // per)

    def batches(self, batch_size: int) -> Iterator[Batch]:
        buf: list[np.ndarray] = []
        buf_start = 0
        buf_pos: int | None = None
        for i in range(self.n_frames):
            if self._stop.is_set():
                break
            if self.produce_delay_s:
                time.sleep(self.produce_delay_s)
            pos = self._position_of(i)
            if buf and pos != buf_pos:
                yield Batch(np.stack(buf), buf_start, buf_pos)
                self._read += len(buf)
                buf = []
            if not buf:
                buf_start = i
                buf_pos = pos
            buf.append(self._make_frame())
            if len(buf) >= batch_size:
                yield Batch(np.stack(buf), buf_start, buf_pos)
                self._read += len(buf)
                buf = []
        if buf:
            yield Batch(np.stack(buf), buf_start, buf_pos)
            self._read += len(buf)


class NdTiffDatasetFrameSource(FrameSource):
    """Live-read a pycromanager NDTiff dataset (``Acquisition.get_dataset()``).

    In the PycroFlow-spawned instrument path the driver runs a pycromanager
    ``Acquisition`` — which writes an **NDTiff v3** dataset into a ``<name>_<n>/``
    subfolder (NOT a flat OME-TIFF in the run dir, so the ``tiff-tail`` glob never
    finds it) — and its ``get_dataset()`` returns an **ndstorage** ``Dataset``
    readable *while the acquisition is still writing*. This source tails that
    dataset along the ``time`` axis (the driver acquires ``num_time_points``
    frames), reading each frame as it lands, yielding contiguous batches, and
    stopping once the dataset reports ``is_finished()`` and no more frames appear.

    Lossless (reads every written frame); single position (``position=None``).
    ndstorage is NOT imported here — the live ``Dataset`` is handed in (same
    process as the ``Acquisition``), so this module still imports on dev / CI
    without ndstorage/pycromanager. The dataset need only expose
    ``has_image``/``read_image``/``is_finished`` (+ optional ``await_new_image``),
    which also lets a fake dataset drive it in hermetic tests.
    """

    lossless = True

    def __init__(
        self,
        dataset,
        *,
        pixelsize_nm: float | None = None,
        time_axis: str = "time",
        poll_s: float = 0.05,
        idle_grace_s: float = 2.0,
        first_frame_timeout_s: float = 120.0,
    ) -> None:
        self._ds = dataset
        self._pixelsize_nm = pixelsize_nm
        self._axis = time_axis
        self._poll_s = poll_s
        # After is_finished(), wait this long for a final frame whose index is
        # not yet visible before declaring the stream done.
        self._idle_grace_s = idle_grace_s
        # If the FIRST frame never arrives within this window, stop instead of
        # awaiting forever — a not-producing camera / stuck MDA (contention, MM
        # GUI holding the camera) otherwise hangs until the outer watchdog.
        self._first_frame_timeout_s = first_frame_timeout_s
        #: Set True if we gave up waiting for the first frame (diagnostics).
        self.no_frames_timed_out = False
        self._read = 0
        self._stop = threading.Event()

    def camera_info(self) -> dict:
        info: dict = {}
        if self._pixelsize_nm:
            info["Pixelsize"] = float(self._pixelsize_nm)
        return info

    def frames_read(self) -> int:
        return self._read

    def close(self) -> None:
        self._stop.set()

    def _has(self, i: int) -> bool:
        try:
            return bool(self._ds.has_image(**{self._axis: i}))
        except Exception:
            return False

    def _finished(self) -> bool:
        try:
            return bool(self._ds.is_finished())
        except Exception:
            return False

    def _await(self) -> None:
        # Event-driven wait when the dataset supports it; else poll-sleep.
        aw = getattr(self._ds, "await_new_image", None)
        if callable(aw):
            try:
                aw(timeout=self._poll_s)
                return
            except Exception:
                pass
        time.sleep(self._poll_s)

    def batches(self, batch_size: int) -> Iterator[Batch]:
        buf: list = []
        start = 0
        t0 = time.monotonic()
        while not self._stop.is_set():
            i = self._read + len(buf)
            if self._has(i):
                try:
                    img = np.asarray(self._ds.read_image(**{self._axis: i}))
                except Exception:
                    # index registered but bytes not flushed yet; retry it.
                    self._await()
                    continue
                buf.append(img)
                if len(buf) >= batch_size:
                    yield Batch(np.stack(buf, axis=0), start, None)
                    self._read += len(buf)
                    start = self._read
                    buf = []
                continue
            # frame i not present yet
            # Fail fast if the acquisition never produced a single frame (camera
            # not triggering / MDA stuck / device contention) — else we await
            # until the outer watchdog (minutes wasted).
            if (
                self._read == 0
                and not buf
                and self._first_frame_timeout_s
                and (time.monotonic() - t0) > self._first_frame_timeout_s
            ):
                self.no_frames_timed_out = True
                break
            if self._finished():
                deadline = time.monotonic() + self._idle_grace_s
                while (
                    not self._has(i)
                    and not self._stop.is_set()
                    and time.monotonic() < deadline
                ):
                    time.sleep(self._poll_s)
                if not self._has(i):
                    break
                continue
            self._await()
        if buf:
            yield Batch(np.stack(buf, axis=0), start, None)
            self._read += len(buf)


class ImageQueueFrameSource(FrameSource):
    """Live frames pushed from a pycromanager ``image_process_fn`` into a queue.

    The canonical pycromanager live path — the SAME hook ``PycroFlow.imaging``
    uses in production (proven on the rig): the Java backend calls
    ``image_process_fn(img, meta, event_queue)`` per acquired frame; the driver
    pushes each ``img`` onto ``frame_q`` and returns ``(img, meta)`` so the frame
    still saves to disk. This source drains that queue into contiguous batches.
    A ``None`` sentinel marks the end of acquisition.

    Chosen over tailing the on-disk NDTiff because ``acq.get_dataset()``'s
    ndstorage live view did NOT surface frames the Java backend was writing
    (``has_image``/``await_new_image`` never fired), so the reader hung though
    the MDA was producing. The queue is **unbounded** so the producer never
    blocks acquisition and never drops a frame (lossless); for a bounded test
    (n_frames) it drains as localization proceeds. Single position
    (``position=None``); ``queue`` / numpy only — no pycromanager import here.
    """

    lossless = True

    def __init__(
        self,
        frame_q,
        *,
        pixelsize_nm: float | None = None,
        first_frame_timeout_s: float = 120.0,
        poll_s: float = 0.2,
    ) -> None:
        self._q = frame_q
        self._pixelsize_nm = pixelsize_nm
        self._first_frame_timeout_s = first_frame_timeout_s
        self._poll_s = poll_s
        self.no_frames_timed_out = False
        self._read = 0
        self._stop = threading.Event()

    def camera_info(self) -> dict:
        info: dict = {}
        if self._pixelsize_nm:
            info["Pixelsize"] = float(self._pixelsize_nm)
        return info

    def frames_read(self) -> int:
        return self._read

    def close(self) -> None:
        self._stop.set()

    def batches(self, batch_size: int) -> Iterator[Batch]:
        buf: list = []
        start = 0
        got_first = False
        t0 = time.monotonic()
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=self._poll_s)
            except queue.Empty:
                if (
                    not got_first
                    and self._first_frame_timeout_s
                    and (time.monotonic() - t0) > self._first_frame_timeout_s
                ):
                    # No first frame ever — fail fast (queue.get's timeout keeps
                    # this loop live, unlike a blocking ndstorage await).
                    self.no_frames_timed_out = True
                    break
                continue
            if item is None:  # sentinel — acquisition finished
                break
            got_first = True
            buf.append(np.asarray(item))
            if len(buf) >= batch_size:
                yield Batch(np.stack(buf, axis=0), start, None)
                self._read += len(buf)
                start = self._read
                buf = []
        if buf:
            yield Batch(np.stack(buf, axis=0), start, None)
            self._read += len(buf)


# ── factory ─────────────────────────────────────────────────────────────────

SOURCE_TIFF_TAIL = "tiff-tail"
SOURCE_RAM_PEEK = "ram-peek"
SOURCE_MOCK = "mock"
SOURCE_NDTIFF_DATASET = "ndtiff-dataset"
SOURCE_IMAGE_QUEUE = "image-queue"


def make_frame_source(kind: str, **kwargs) -> FrameSource:
    """Construct a frame source by name.

    ``"tiff-tail"`` is the lossless default (needs ``acq_dir``); ``"ram-peek"``
    is the lossy live-view source; ``"mock"`` is for hermetic tests.
    """
    if kind == SOURCE_TIFF_TAIL:
        return TiffTailFrameSource(**kwargs)
    if kind == SOURCE_RAM_PEEK:
        return RamPeekFrameSource(**kwargs)
    if kind == SOURCE_MOCK:
        return MockFrameSource(**kwargs)
    if kind == SOURCE_NDTIFF_DATASET:
        return NdTiffDatasetFrameSource(**kwargs)
    if kind == SOURCE_IMAGE_QUEUE:
        return ImageQueueFrameSource(**kwargs)
    raise ValueError("unknown frame source {!r}".format(kind))
