"""FrameTap — the production-acquisition seam feeding live analysis.

WP-LIVE-INT: ``ImagingSystem.record_movie`` brackets each FOV with
:meth:`FrameTap.start_fov` / :meth:`FrameTap.end_fov`, and its
``image_process_fn`` pushes every frame, so the WP-4 live pipeline consumes
the ORCHESTRATED acquisition instead of driving its own MDA
(:class:`~PycroFlow.live_analysis.acquisition_driver.AcquisitionDriver`
remains the standalone ``--live`` / harness path). The tap is deliberately
dumb — a fresh unbounded per-FOV queue (drained by
:class:`~PycroFlow.live_analysis.frame_source.ImageQueueFrameSource`, ``None``
sentinel on FOV end) plus one event — and every producer-side call is
non-blocking and exception-proof, so the acquisition hot path only ever pays
a queue put.

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


class FrameTap:
    """Per-FOV frame queue + FOV lifecycle notifications for live analysis.

    Parameters
    ----------
    on_fov_start : callable or None
        Called as ``on_fov_start(acq_name, acquisition_config, frame_q)``
        from the acquisition thread when a FOV begins. Exceptions are
        swallowed (logged) — a consumer bug must never reach the acquisition.
    on_fov_end : callable or None
        Called with no arguments after the FOV's sentinel is enqueued.
    """

    def __init__(self, *, on_fov_start=None, on_fov_end=None):
        self._on_fov_start = on_fov_start
        self._on_fov_end = on_fov_end
        self._q: queue.Queue | None = None
        self._fov_end = threading.Event()
        self._lock = threading.Lock()

    # -- producer side (acquisition thread; never blocks, never raises) -----

    def start_fov(self, acq_name, acquisition_config) -> queue.Queue:
        """Open a fresh frame queue for one FOV and notify the consumer."""
        with self._lock:
            self._fov_end.clear()
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
        """Enqueue one frame (a copy, so the backend may reuse its buffer)."""
        q = self._q
        if q is None:
            return
        try:
            item = img.copy() if hasattr(img, "copy") else img
            q.put_nowait(item)
        except Exception as exc:  # noqa: BLE001 - tee must never hurt acq
            logger.warning("frame tap push failed: {!r}".format(exc))

    def end_fov(self) -> None:
        """Enqueue the end-of-FOV sentinel and notify the consumer."""
        with self._lock:
            q, self._q = self._q, None
        if q is not None:
            try:
                q.put_nowait(None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("frame tap sentinel failed: {!r}".format(exc))
        if self._on_fov_end is not None:
            try:
                self._on_fov_end()
            except Exception as exc:  # noqa: BLE001
                logger.warning("frame tap on_fov_end raised: {!r}".format(exc))

    # -- early-abort handshake ----------------------------------------------

    def request_fov_end(self) -> None:
        """Ask the acquisition to end the current FOV early (keep frames)."""
        self._fov_end.set()

    def fov_end_requested(self) -> bool:
        """Checked by ``image_process_fn``; True ends the current MDA."""
        return self._fov_end.is_set()
