"""Frame-source backends for the WP-1 performance harness.

The harness measures the *same* quantities in every mode; only the frame
source differs, and that difference is confined to this module behind a single
small interface, :class:`FrameSourceBackend`:

* :class:`EmulatedBackend` — a pure-stdlib producer / consumer / bounded-buffer
  simulation. No instrument, no vendor SDK; this is the dry run validated in
  CI. It models the exact physics under test (a bounded Micro-Manager circular
  buffer filled by the camera and drained by the save pipeline, with the
  incremental reader contending for I/O with the writer).
* :class:`InstrumentBackend` — a real pycromanager acquisition whose circular
  buffer occupancy is read from the MM ``Core`` and whose incremental reader
  reads the NDTiff ``Dataset`` MM is writing. Exercised only on the Windows
  acquisition PC; pycromanager is imported lazily so this module imports fine
  on dev / CI where the SDK is absent (mocked).

Both expose identical query methods, so :mod:`PycroFlow.perf.harness` samples
occupancy, drives the batch reader, and computes metrics with one code path.
"""

from __future__ import annotations

import abc
import os
import sys
import threading
import time

from PycroFlow.perf.config import (
    MODE_EMULATOR,
    MODE_INSTRUMENT,
    READER_PROCESS,
    PerfConfig,
)


class FrameSourceBackend(abc.ABC):
    """Interface the harness drives; the only thing that differs by mode.

    A backend produces ``n_frames`` frames into a bounded buffer at the target
    rate, a writer drains that buffer (this is "write throughput"), and the
    harness's reader pulls contiguous batches of already-written frames back
    out via :meth:`read_batch`. Query methods are safe to call concurrently
    from the monitor / reader threads.
    """

    @abc.abstractmethod
    def start(self) -> None:
        """Begin producing frames (non-blocking)."""

    @abc.abstractmethod
    def occupancy(self) -> int:
        """Frames currently in the circular buffer (produced, not written)."""

    @abc.abstractmethod
    def capacity(self) -> int:
        """Circular-buffer capacity, in frames."""

    @abc.abstractmethod
    def produced(self) -> int:
        """Frames successfully placed in the buffer so far."""

    @abc.abstractmethod
    def written(self) -> int:
        """Frames drained from the buffer (written to disk) so far."""

    @abc.abstractmethod
    def dropped(self) -> int:
        """Frames lost because the buffer was full when produced."""

    @abc.abstractmethod
    def available_for_read(self) -> int:
        """Written frames not yet consumed by :meth:`read_batch`."""

    @abc.abstractmethod
    def read_batch(self, n: int) -> int:
        """Read the next ``n`` available frames; return the count read."""

    @abc.abstractmethod
    def production_done(self) -> bool:
        """True once every frame has been produced (or dropped)."""

    @abc.abstractmethod
    def all_written(self) -> bool:
        """True once production is done and the buffer is fully drained."""

    @abc.abstractmethod
    def describe(self) -> dict:
        """Return backend provenance for ``run_meta.json``."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources / stop threads."""

    # --- Optional external (out-of-process) reader ---------------------
    # By default the harness drives the reader itself via :meth:`read_batch`
    # (used by the emulator and the in-process/thread instrument reader). A
    # backend that manages its own separate-process reader overrides these and
    # returns True from :meth:`external_reader`, and the harness then starts /
    # stops that reader instead of calling :meth:`read_batch`.
    def external_reader(self) -> bool:
        """True if this backend manages its own out-of-process reader."""
        return False

    def start_external_reader(self, batch_size: int) -> None:
        """Start the self-managed external reader (no-op by default)."""

    def stop_external_reader(self) -> None:
        """Stop the self-managed external reader (no-op by default)."""

    def reader_frames_read(self) -> int | None:
        """Frames the external reader read, or None if not applicable."""
        return None

    def acquisition_error(self) -> BaseException | None:
        """Return an exception the frame source raised, if any.

        Lets the harness distinguish a genuine end-of-acquisition from a frame
        source that died (e.g. the disk filled mid-acquisition) so it can stop
        the sweep and free the partial data instead of hanging.
        """
        return None


class EmulatedBackend(FrameSourceBackend):
    """Rate-integrating producer/consumer/bounded-buffer simulation.

    A single engine thread integrates production and writing over the
    *measured* elapsed time each tick, so the buffer dynamics stay correct
    even under coarse OS sleep timers (important for Windows CI). While the
    reader holds a batch (see :meth:`read_batch`) the writer's drain rate is
    divided by ``1 + contention``, so a slow reader / large batch drives buffer
    occupancy up and, if the buffer fills, causes dropped frames — the
    back-pressure the harness must detect.
    """

    def __init__(self, cfg: PerfConfig):
        self.cfg = cfg
        self.ep = cfg.emulator
        self._cap = cfg.buffer_capacity_frames()
        self._n = cfg.n_frames
        self._lock = threading.Lock()
        self._occ = 0
        self._produced = 0
        self._attempted = 0
        self._written = 0
        self._read = 0
        self._dropped = 0
        self._readers_active = 0
        self._pcredit = 0.0
        self._wcredit = 0.0
        self._stop = threading.Event()
        self._engine: threading.Thread | None = None
        self._last = 0.0

    def start(self) -> None:
        self._last = time.perf_counter()
        self._engine = threading.Thread(
            target=self._run_engine, name="perf-emu-engine", daemon=True
        )
        self._engine.start()

    def _run_engine(self) -> None:
        fr = self.cfg.frame_rate_hz
        base_write_rate = fr * self.ep.write_speed_factor
        tick = self.ep.tick_s
        while not self._stop.is_set():
            now = time.perf_counter()
            dt = now - self._last
            self._last = now
            with self._lock:
                readers = self._readers_active
                # Produce: fill the buffer at the frame rate; a frame arriving
                # to a full buffer is dropped (circular-buffer overflow).
                self._pcredit += fr * dt
                while self._pcredit >= 1.0 and self._attempted < self._n:
                    self._pcredit -= 1.0
                    self._attempted += 1
                    if self._occ < self._cap:
                        self._occ += 1
                        self._produced += 1
                    else:
                        self._dropped += 1
                # Write: drain the buffer; the reader slows the writer while a
                # batch is in flight.
                factor = 1.0 + (self.ep.contention if readers else 0.0)
                self._wcredit += (base_write_rate / factor) * dt
                while self._wcredit >= 1.0 and self._occ > 0:
                    self._wcredit -= 1.0
                    self._occ -= 1
                    self._written += 1
                # A writer cannot bank drain capacity while the buffer is
                # empty, so clamp the leftover credit.
                if self._occ == 0 and self._wcredit > 1.0:
                    self._wcredit = 1.0
                done = self._attempted >= self._n and self._occ == 0
            if done:
                break
            time.sleep(tick)

    def occupancy(self) -> int:
        with self._lock:
            return self._occ

    def capacity(self) -> int:
        return self._cap

    def produced(self) -> int:
        with self._lock:
            return self._produced

    def written(self) -> int:
        with self._lock:
            return self._written

    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def available_for_read(self) -> int:
        with self._lock:
            return self._written - self._read

    def read_batch(self, n: int) -> int:
        if n <= 0:
            return 0
        with self._lock:
            avail = self._written - self._read
            n = min(n, avail)
            if n <= 0:
                return 0
            self._read += n
            self._readers_active += 1
        try:
            # Reading n frames costs real time and, while it runs, contends
            # with the writer (see _run_engine).
            time.sleep(self.ep.read_cost_per_frame_s * n)
        finally:
            with self._lock:
                self._readers_active -= 1
        return n

    def production_done(self) -> bool:
        with self._lock:
            return self._attempted >= self._n

    def all_written(self) -> bool:
        with self._lock:
            return self._attempted >= self._n and self._occ == 0

    def describe(self) -> dict:
        return {
            "backend": "emulator",
            "write_speed_factor": self.ep.write_speed_factor,
            "contention": self.ep.contention,
            "read_cost_per_frame_s": self.ep.read_cost_per_frame_s,
            "tick_s": self.ep.tick_s,
        }

    def close(self) -> None:
        self._stop.set()
        if self._engine is not None:
            self._engine.join(timeout=5.0)


class InstrumentBackend(FrameSourceBackend):  # pragma: no cover
    """Real pycromanager acquisition + incremental NDTiff reader.

    Runs only on the Windows acquisition PC (pycromanager is imported lazily so
    dev / CI, where the SDK is mocked, can still import this module). Circular
    buffer occupancy is read straight from the MM ``Core``
    (``get_remaining_image_count`` / ``get_buffer_total_capacity``); the
    incremental reader reads the NDTiff ``Dataset`` MM is already writing
    (option (b) from the design), which is exactly the coupling under test.

    This path is intentionally not covered by the hermetic test suite — it is
    validated by the on-instrument round-trip described in
    ``docs/WP-1-RUNBOOK.md``.
    """

    def __init__(self, cfg: PerfConfig, tag: str = "run"):
        self.cfg = cfg
        self._tag = tag
        self._n = cfg.n_frames
        self._lock = threading.Lock()
        self._core = None
        self._acq = None
        self._dataset = None
        self._acq_thread: threading.Thread | None = None
        self._acq_dir: str | None = None
        self._written = 0
        self._read = 0
        self._capacity = 0
        self._done = threading.Event()
        self._acq_error: BaseException | None = None
        self._camera = ""
        self._mm_version = ""
        # Separate-process reader (reader_mode == "process").
        self._reader_proc = None
        self._reader_stop_file: str | None = None
        self._reader_count_file: str | None = None
        self._reader_read: int | None = None

    def _resolve_data_dir(self) -> str:
        # Raw NDTiff is large; it must NOT land in the repo. Require an
        # explicit data_dir (a large data drive) rather than silently writing
        # tens of GB next to the committed results.
        if not self.cfg.data_dir:
            raise ValueError(
                "instrument mode needs 'data_dir' set to a path on a large "
                "data drive (NOT the repo) for the raw acquisition; got None"
            )
        return self.cfg.data_dir

    def start(self) -> None:
        from pycromanager import Acquisition, multi_d_acquisition_events

        from PycroFlow.services import mm_core

        self._core = mm_core.get_core()
        # Size the circular buffer to the configured MB footprint (matches
        # Micro-Manager's "sequence buffer size") and apply the ROI.
        try:
            self._core.set_circular_buffer_memory_footprint(
                int(self.cfg.buffer_mb)
            )
        except Exception:
            pass
        if self.cfg.roi is not None:
            try:
                x, y, w, h = self.cfg.roi
                self._core.set_roi(x, y, w, h)
            except Exception:
                pass
        try:
            self._camera = self._core.get_camera_device()
            self._mm_version = self._core.get_version_info()
        except Exception:
            pass
        try:
            self._capacity = int(self._core.get_buffer_total_capacity())
        except Exception:
            self._capacity = self.cfg.buffer_capacity_frames()

        # Unique raw-acquisition dir on the data drive, deleted after
        # measurement unless keep_raw_data.
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self._acq_dir = os.path.join(
            self._resolve_data_dir(),
            "wp1_raw_{}_{}".format(self._tag, stamp),
        )
        os.makedirs(self._acq_dir, exist_ok=True)

        events = multi_d_acquisition_events(
            num_time_points=self._n,
            time_interval_s=0,
            channel_exposures_ms=[self.cfg.exposure_ms],
            order="tcpz",
        )

        def _run() -> None:
            # Capture any failure (e.g. the disk filling mid-acquisition) and
            # ALWAYS signal completion, so the harness never hangs waiting on a
            # frame source that has already died.
            try:
                with Acquisition(
                    directory=self._acq_dir,
                    name="acq",
                    show_display=False,
                    image_process_fn=self._count_frame,
                ) as acq:
                    self._acq = acq
                    acq.acquire(events)
                    self._dataset = acq.get_dataset()
            except BaseException as exc:  # noqa: BLE001 - surfaced to harness
                self._acq_error = exc
            finally:
                self._done.set()

        self._acq_thread = threading.Thread(
            target=_run, name="perf-instr-acq", daemon=True
        )
        self._acq_thread.start()

    def _count_frame(self, img, meta, event_queue):
        with self._lock:
            self._written += 1
        return img, meta

    def occupancy(self) -> int:
        try:
            return int(self._core.get_remaining_image_count())
        except Exception:
            return 0

    def capacity(self) -> int:
        return self._capacity or self.cfg.buffer_capacity_frames()

    def produced(self) -> int:
        return self.written() + self.occupancy()

    def written(self) -> int:
        with self._lock:
            return self._written

    def dropped(self) -> int:
        # MM does not expose a running drop count; frames that never reach the
        # save pipeline once production is complete are the dropped ones.
        if not self._done.is_set():
            return 0
        return max(0, self._n - self.written())

    def available_for_read(self) -> int:
        if self._dataset is None:
            return 0
        return max(0, self.written() - self._read)

    def read_batch(self, n: int) -> int:
        if n <= 0 or self._dataset is None:
            return 0
        read = 0
        for _ in range(n):
            idx = self._read
            try:
                if not self._dataset.has_image(t=idx):
                    break
                # Force an actual incremental read of the just-written frame.
                self._dataset.read_image(t=idx)
            except Exception:
                break
            self._read += 1
            read += 1
        return read

    def production_done(self) -> bool:
        return self._done.is_set()

    def all_written(self) -> bool:
        return self._done.is_set()

    def external_reader(self) -> bool:
        return self.cfg.reader_mode == READER_PROCESS

    def start_external_reader(
        self, batch_size: int
    ) -> None:  # pragma: no cover - launches a real reader subprocess
        import subprocess
        import tempfile

        base = tempfile.mkdtemp(prefix="wp1_reader_")
        self._reader_stop_file = os.path.join(base, "stop")
        self._reader_count_file = os.path.join(base, "count")
        self._reader_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "PycroFlow.perf.reader_process",
                "--acq-dir",
                str(self._acq_dir),
                "--batch",
                str(batch_size),
                "--stop-file",
                self._reader_stop_file,
                "--count-file",
                self._reader_count_file,
                "--poll",
                str(self.cfg.reader_poll_s),
                "--reopen-interval",
                str(self.cfg.reader_reopen_interval_s),
            ]
        )

    def stop_external_reader(
        self,
    ) -> None:  # pragma: no cover - tears down the reader subprocess
        if self._reader_proc is None:
            return
        # Signal stop by creating the stop-file, then wait for a clean exit.
        try:
            if self._reader_stop_file:
                open(self._reader_stop_file, "w").close()
            self._reader_proc.wait(timeout=60.0)
        except Exception:
            self._reader_proc.terminate()
        finally:
            self._reader_read = self._read_reader_count()
            self._reader_proc = None

    def _read_reader_count(self) -> int | None:  # pragma: no cover
        if not self._reader_count_file:
            return None
        try:
            with open(self._reader_count_file, encoding="utf-8") as fh:
                return int(fh.read().strip() or 0)
        except (OSError, ValueError):
            return None

    def reader_frames_read(self) -> int | None:
        return self._reader_read

    def acquisition_error(self) -> BaseException | None:
        return self._acq_error

    def describe(self) -> dict:
        return {
            "backend": "instrument",
            "reader_mode": self.cfg.reader_mode,
            "reader_frames_read": self._reader_read,
            "camera": self._camera,
            "mm_version": self._mm_version,
            "exposure_ms": self.cfg.exposure_ms,
            "roi": self.cfg.roi,
            "raw_data_dir": self._acq_dir,
            "raw_data_kept": self.cfg.keep_raw_data,
        }

    def close(self) -> None:
        if self._reader_proc is not None:  # pragma: no cover
            self.stop_external_reader()
        if self._acq_thread is not None:
            self._acq_thread.join(timeout=30.0)
        # Free the (large) raw acquisition unless explicitly kept: WP-1 only
        # needs the metrics, and a full sweep would otherwise accumulate one
        # movie per configuration.
        if (
            not self.cfg.keep_raw_data
            and self._acq_dir is not None
            and os.path.isdir(self._acq_dir)
        ):
            self._delete_raw_dir()

    def _delete_raw_dir(self) -> None:  # pragma: no cover - Windows FS timing
        """Delete this configuration's raw acquisition, honestly.

        NDTiff files are memory-mapped while a ``Dataset`` is open, and Windows
        refuses to unlink an open/mapped file. So we must (1) close our reader
        handle on the dataset first, and (2) NOT swallow deletion failures:
        ``shutil.rmtree(ignore_errors=True)`` would leave tens of GB on disk
        while falsely reporting success, which is exactly what fills a small
        local drive mid-sweep. Retry briefly (handles can take a moment to
        release), then report the true outcome so a leftover is visible.
        """
        import shutil

        # Release any NDTiff reader handle so the OS lets us delete the files.
        if self._dataset is not None:
            try:
                self._dataset.close()
            except Exception:
                pass
            self._dataset = None

        target = self._acq_dir
        last_err: Exception | None = None
        for _ in range(10):
            try:
                shutil.rmtree(target)
            except FileNotFoundError:
                break
            except OSError as exc:
                last_err = exc
                time.sleep(0.5)
            if not os.path.isdir(target):
                break

        if os.path.isdir(target):
            print(
                "[wp1] WARNING: could NOT delete raw acquisition {} ({}); "
                "delete it manually to free disk before the next run".format(
                    target, last_err
                ),
                flush=True,
            )
        else:
            # Visible confirmation the (large) raw movie was really freed, so a
            # small local drive never accumulates more than one acquisition.
            print(
                "[wp1] deleted raw acquisition: {}".format(target),
                flush=True,
            )


def make_backend(cfg: PerfConfig, tag: str = "run") -> FrameSourceBackend:
    """Construct the backend for ``cfg.mode``.

    Parameters
    ----------
    cfg : PerfConfig
        The mode-resolved configuration.
    tag : str
        Short label for this configuration, used to name the instrument
        backend's raw-acquisition directory uniquely.
    """
    if cfg.mode == MODE_EMULATOR:
        return EmulatedBackend(cfg)
    if cfg.mode == MODE_INSTRUMENT:
        return InstrumentBackend(cfg, tag=tag)
    raise ValueError("unknown mode {!r}".format(cfg.mode))
