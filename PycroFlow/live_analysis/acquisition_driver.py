"""Drive a real pycromanager MDA that feeds the WP-4 live pipeline.

Shared by the Gate-2 harness (:mod:`PycroFlow.perf.gate2_harness`) and the live
GUI's ``--live`` mode (:mod:`PycroFlow.gui.live`): both need to start a real
acquisition on the instrument and stream its frames into a
:class:`~PycroFlow.live_analysis.service.LiveAnalysisService` — one via headless
checks, the other with the operator GUI attached — so the acquisition primitive
lives here, in one place.

It spawns the acquisition in a **background thread** and pushes each frame to an
in-memory queue via pycromanager's ``image_process_fn`` (the same hook
:mod:`PycroFlow.imaging` uses in production), which an
:class:`~PycroFlow.live_analysis.frame_source.ImageQueueFrameSource` drains. The
frame also stays in the save pipeline (the fn returns ``(img, meta)``), so the
NDTiff still lands on disk for the archive. pycromanager + the MM core are
imported lazily so importing this module never requires them.
"""

from __future__ import annotations

import os
import queue
import threading
import time


class AcquisitionDriver:
    """Start a real MDA in a background thread, streaming frames to a queue.

    Reuses PycroFlow's shared Micro-Manager Core
    (:func:`PycroFlow.services.mm_core.get_core`, the same connection the GUI /
    monet use) + pycromanager's ``Acquisition`` / ``multi_d_acquisition_events``
    — the identical primitive ``PycroFlow.imaging`` uses. The full
    ``ImagingSystem`` is not reused because it needs a protocol + PFS config the
    Gate-2 / live-preview paths do not have.

    Parameters
    ----------
    data_dir : str
        Directory the NDTiff dataset is written to (a large data drive, not the
        repo).
    n_frames : int
        Number of time points to acquire.
    exposure_ms : float
        Per-frame exposure.
    enabled : bool
        When False the driver is a **no-op** (``start`` completes immediately) —
        used by the harness's emulator mode, where ``MockFrameSource`` fabricates
        frames and no MDA is wanted.
    name : str
        Acquisition name (the NDTiff subfolder prefix under ``data_dir``).
    """

    def __init__(
        self,
        data_dir,
        n_frames,
        exposure_ms,
        *,
        enabled: bool = True,
        name: str = "live_raw",
    ) -> None:
        self.data_dir = data_dir
        self.n_frames = n_frames
        self.exposure_ms = exposure_ms
        self.enabled = enabled
        self.name = name
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._started = threading.Event()
        self._done = threading.Event()
        self._acq = None
        self._latest = None
        # Live frames pushed from image_process_fn. Unbounded so the callback
        # never blocks acquisition; the ImageQueueFrameSource drains it. A None
        # sentinel is enqueued when acquisition ends so the reader stops.
        self._frame_q: "queue.Queue" = queue.Queue()

    def get_frame_queue(self) -> "queue.Queue":
        return self._frame_q

    def latest_frame(self):
        """The most recent acquired frame (ndarray) or None — a cheap preview
        peek for the GUI's Overview, independent of the (consumed) frame queue.
        """
        return self._latest

    def _on_image(self, img, meta, event_queue):  # pragma: no cover - acq PC
        """image_process_fn: push each frame to the reader, keep saving to disk.

        Mirrors ``PycroFlow.imaging``'s proven ``image_process_fn(img, meta,
        event_queue)`` contract — returns ``(img, meta)`` so the frame stays in
        the save pipeline (disk + archive), and additionally enqueues a copy for
        the live localize pipeline. Never raises into the acquisition.
        """
        try:
            import numpy as _np

            arr = _np.array(img, copy=True)
            self._latest = arr  # cheap preview peek (ref reassignment)
            self._frame_q.put_nowait(arr)
        except Exception:
            pass
        return (img, meta)

    def is_noop(self) -> bool:
        return not self.enabled

    def start(self) -> None:
        """Start acquiring in a background thread (no-op when disabled)."""
        if self.is_noop():
            self._done.set()
            return
        self._thread = threading.Thread(  # pragma: no cover - acq PC only
            target=self._run, name="live-acq", daemon=True
        )
        self._thread.start()  # pragma: no cover - acq PC only

    def _run(self) -> None:  # pragma: no cover - acq PC only (needs MM)
        try:
            from pycromanager import (
                Acquisition,
                multi_d_acquisition_events,
            )

            from PycroFlow.services import mm_core

            core = mm_core.get_core()
            try:
                core.set_exposure(float(self.exposure_ms))
            except Exception:
                pass
            os.makedirs(self.data_dir, exist_ok=True)
            events = multi_d_acquisition_events(
                num_time_points=int(self.n_frames),
                time_interval_s=0,
                channel_exposures_ms=[float(self.exposure_ms)],
                order="tcpz",
            )
            self._started.set()
            with Acquisition(
                directory=self.data_dir,
                name=self.name,
                show_display=False,
                image_process_fn=self._on_image,
            ) as acq:
                self._acq = acq
                acq.acquire(events)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the driver
            self._error = exc
        finally:
            self._started.set()
            self._done.set()
            # Unblock the reader whether acquisition finished or errored.
            try:
                self._frame_q.put_nowait(None)
            except Exception:
                pass

    def error(self) -> BaseException | None:
        return self._error

    def wait(self, timeout: float | None = None) -> bool:
        """Block until acquisition finishes; True if it completed in time."""
        return self._done.wait(timeout=timeout)

    def get_dataset(self, timeout: float = 60.0):  # pragma: no cover - acq PC
        """Return the Acquisition's live ndstorage ``Dataset`` (or None).

        The dataset only exists once ``_run`` has entered ``with Acquisition``.
        Poll until it's available (or the acquisition errored / finished with no
        dataset), returning None on timeout so the caller can fall back instead
        of crashing.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._error is not None:
                return None
            acq = self._acq
            if acq is not None:
                try:
                    return acq.get_dataset()
                except Exception:
                    pass  # created but dataset not ready yet — keep polling
            elif self._done.is_set():
                return None  # finished/failed before an Acquisition existed
            time.sleep(0.1)
        return None

    def close(self) -> None:
        """Best-effort teardown of the acquisition thread."""
        if self._thread is not None:  # pragma: no cover - acq PC only
            self._thread.join(timeout=10.0)


def image_queue_source_kwargs(
    driver: AcquisitionDriver, camera_info: dict, first_frame_timeout_s: float
) -> dict:
    """Build ``SOURCE_IMAGE_QUEUE`` kwargs for a driver-fed FOV.

    The frames come from the acquisition's ``image_process_fn`` (via the driver's
    queue), not the tiff-tail source — the latter globs ``data_dir`` and can't
    find the queue-fed frames. ``camera_info`` must carry the full picasso
    photon-conversion keys (Baseline / Sensitivity / Gain / Qe / Pixelsize) or
    the fit raises on every frame.
    """
    return {
        "frame_q": driver.get_frame_queue(),
        "camera_info": dict(camera_info),
        "first_frame_timeout_s": first_frame_timeout_s,
    }
