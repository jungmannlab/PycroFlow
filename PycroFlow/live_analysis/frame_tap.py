"""FrameTap — the production-acquisition seam feeding live analysis.

WP-LIVE-INT: ``ImagingSystem.record_movie`` brackets each FOV with
:meth:`FrameTap.start_fov` / :meth:`FrameTap.end_fov`, and its
``image_process_fn`` pushes every frame, so the WP-4 live pipeline consumes
the ORCHESTRATED acquisition instead of driving its own MDA
(:class:`~PycroFlow.live_analysis.acquisition_driver.AcquisitionDriver`
remains the standalone ``--live`` / harness path). The tap is deliberately
dumb — a fresh per-FOV queue (drained by
:class:`~PycroFlow.live_analysis.frame_source.ImageQueueFrameSource`, ``None``
sentinel on FOV end) plus one event — and every producer-side call is
non-blocking and exception-proof, so the acquisition hot path only ever pays
a copy + queue put.

Backpressure: the per-FOV queue is SOFT-BOUNDED (``max_pending_frames``).
When the live pipeline lags that far behind, further frames are DROPPED from
the live stream (counted + logged loudly) rather than accumulated — an
unbounded backlog of raw frames in RAM can OOM the acquisition PC, and
blocking the producer is forbidden. Dropping only degrades the LIVE view;
the authoritative raw movie on disk is untouched. (WP-4's lossless
``TiffTailFrameSource``, which keeps the backlog on disk, remains the path
for runs that must live-localize every frame.)

Early-abort (initiative #3): a consumer (the live-run coordinator, reacting
to the service's ``abort_requested``) calls :meth:`request_fov_end`;
``image_process_fn`` checks :meth:`fov_end_requested` and ends the CURRENT
MDA via ``event_queue.put(None)`` — the frames acquired so far stay in the
save pipeline, and the protocol continues with its next entry. This is
distinct from the orchestrator Abort, which is unchanged.
"""

from __future__ import annotations

import queue
import threading

from loguru import logger

# Soft bound on live-pipeline backlog, in frames (≈2 GB at 2048² uint16,
# ≈130 MB at 512²). Override per setup via the imaging config's
# ``live_analysis: {max_pending_frames: ...}``.
DEFAULT_MAX_PENDING_FRAMES = 256


class FrameTap:
    """Per-FOV frame queue + FOV lifecycle notifications for live analysis.

    Parameters
    ----------
    on_fov_start : callable or None
        Called as ``on_fov_start(acq_name, acquisition_config, frame_q)``
        from the acquisition thread when a FOV begins. Exceptions are
        swallowed (logged) — a consumer bug must never reach the acquisition.
    on_fov_end : callable or None
        Called with no arguments just BEFORE the FOV's sentinel is enqueued,
        so a consumer can stamp per-FOV state (e.g. the finished dataset's
        on-disk path) that the pipeline's drain will read.
    max_pending_frames : int
        Soft bound on frames buffered for the live pipeline; see the module
        docstring. Beyond it, frames are dropped from the live stream and
        counted in :meth:`dropped_frames`.
    """

    def __init__(
        self,
        *,
        on_fov_start=None,
        on_fov_end=None,
        max_pending_frames: int = DEFAULT_MAX_PENDING_FRAMES,
    ):
        self._on_fov_start = on_fov_start
        self._on_fov_end = on_fov_end
        self._max_pending = max(1, int(max_pending_frames))
        self._q: queue.Queue | None = None
        self._fov_end = threading.Event()
        self._lock = threading.Lock()
        self._dropped = 0

    # -- producer side (acquisition thread; never blocks, never raises) -----

    def start_fov(self, acq_name, acquisition_config) -> queue.Queue:
        """Open a fresh frame queue for one FOV and notify the consumer."""
        with self._lock:
            self._fov_end.clear()
            self._dropped = 0
            self._q = queue.Queue()
            q = self._q
        if self._on_fov_start is not None:
            try:
                self._on_fov_start(acq_name, dict(acquisition_config or {}), q)
            except Exception as exc:  # noqa: BLE001 - consumer bug stays out
                logger.warning(
                    "frame tap on_fov_start raised: {!r}".format(exc)
                )
        return q

    def push(self, img, meta) -> None:
        """Enqueue one frame (a copy, so the backend may reuse its buffer).

        Soft-bounded: beyond ``max_pending_frames`` of backlog the frame is
        dropped from the LIVE stream (counted, first drop logged loudly) —
        the queue itself stays unbounded so :meth:`end_fov`'s sentinel can
        never be refused.
        """
        q = self._q
        if q is None:
            return
        try:
            if q.qsize() >= self._max_pending:
                self._dropped += 1
                if self._dropped == 1:
                    logger.error(
                        "live-analysis pipeline lags > {} pending frames; "
                        "dropping frames from the LIVE stream (raw movie on "
                        "disk is unaffected)".format(self._max_pending)
                    )
                return
            item = img.copy() if hasattr(img, "copy") else img
            q.put_nowait(item)
        except Exception as exc:  # noqa: BLE001 - tee must never hurt acq
            logger.warning("frame tap push failed: {!r}".format(exc))

    def end_fov(self) -> None:
        """Notify the consumer, then enqueue the end-of-FOV sentinel."""
        if self._on_fov_end is not None:
            try:
                self._on_fov_end()
            except Exception as exc:  # noqa: BLE001
                logger.warning("frame tap on_fov_end raised: {!r}".format(exc))
        with self._lock:
            q, self._q = self._q, None
            dropped = self._dropped
        if q is not None:
            if dropped:
                logger.error(
                    "frame tap dropped {} frames from the live stream this "
                    "FOV (pipeline lagged; raise max_pending_frames or "
                    "reduce load)".format(dropped)
                )
            try:
                q.put_nowait(None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("frame tap sentinel failed: {!r}".format(exc))

    def feed_frames(self, acq_name, acquisition_config, frames) -> int:
        """Run one synchronous-producer FOV through the tap.

        Centralizes the bracketing protocol the real acquisition implements
        across ``ImagingSystem.record_movie`` / ``image_process_fn`` —
        :meth:`start_fov`, per-frame :meth:`push` honoring
        :meth:`fov_end_requested`, :meth:`end_fov` in a ``finally`` — so
        synchronous producers (the emulated imaging system, tests) cannot
        drift from it.

        Parameters
        ----------
        acq_name : str
            FOV name passed to ``on_fov_start``.
        acquisition_config : dict or None
            Passed through to ``on_fov_start``.
        frames : iterable
            Frames to push, one per acquisition "frame".

        Returns
        -------
        int
            Number of frames actually pushed.
        """
        self.start_fov(acq_name, acquisition_config)
        pushed = 0
        try:
            for frame in frames:
                if self.fov_end_requested():
                    break
                self.push(frame, None)
                pushed += 1
        finally:
            self.end_fov()
        return pushed

    # -- introspection --------------------------------------------------------

    def fov_active(self) -> bool:
        """True between :meth:`start_fov` and :meth:`end_fov`."""
        return self._q is not None

    def dropped_frames(self) -> int:
        """Frames dropped from the live stream in the current/last FOV."""
        return self._dropped

    # -- early-abort handshake ----------------------------------------------

    def request_fov_end(self) -> None:
        """Ask the acquisition to end the current FOV early (keep frames)."""
        self._fov_end.set()

    def fov_end_requested(self) -> bool:
        """Checked by ``image_process_fn``; True ends the current MDA."""
        return self._fov_end.is_set()
