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
import threading
import time

from PycroFlow.perf.config import MODE_EMULATOR, MODE_INSTRUMENT, PerfConfig


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
        self._cap = cfg.buffer_size
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

    def __init__(self, cfg: PerfConfig):
        self.cfg = cfg
        self._n = cfg.n_frames
        self._lock = threading.Lock()
        self._core = None
        self._acq = None
        self._dataset = None
        self._acq_thread: threading.Thread | None = None
        self._written = 0
        self._read = 0
        self._capacity = 0
        self._done = threading.Event()
        self._camera = ""
        self._mm_version = ""

    def start(self) -> None:
        from pycromanager import (
            Acquisition,
            multi_d_acquisition_events,
        )

        from PycroFlow.services import mm_core

        self._core = mm_core.get_core()
        try:
            self._camera = self._core.get_camera_device()
            self._mm_version = self._core.get_version_info()
        except Exception:
            pass
        try:
            self._capacity = int(self._core.get_buffer_total_capacity())
        except Exception:
            self._capacity = self.cfg.buffer_size

        events = multi_d_acquisition_events(
            num_time_points=self._n,
            time_interval_s=0,
            channel_exposures_ms=[self.cfg.exposure_ms],
            order="tcpz",
        )

        def _run() -> None:
            with Acquisition(
                directory=self.cfg.output_dir,
                name="wp1_instrument_acq",
                show_display=False,
                image_process_fn=self._count_frame,
            ) as acq:
                self._acq = acq
                acq.acquire(events)
                self._dataset = acq.get_dataset()
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
        return self._capacity or self.cfg.buffer_size

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

    def describe(self) -> dict:
        return {
            "backend": "instrument",
            "camera": self._camera,
            "mm_version": self._mm_version,
            "exposure_ms": self.cfg.exposure_ms,
            "roi": self.cfg.roi,
        }

    def close(self) -> None:
        if self._acq_thread is not None:
            self._acq_thread.join(timeout=30.0)


def make_backend(cfg: PerfConfig) -> FrameSourceBackend:
    """Construct the backend for ``cfg.mode``."""
    if cfg.mode == MODE_EMULATOR:
        return EmulatedBackend(cfg)
    if cfg.mode == MODE_INSTRUMENT:
        return InstrumentBackend(cfg)
    raise ValueError("unknown mode {!r}".format(cfg.mode))
