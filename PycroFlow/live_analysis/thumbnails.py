"""ThumbnailStreamer — the ONE Overview feeder for live frames.

A small daemon thread that periodically reads the newest frame from a
provider (the MM-preview's peek/mock source, an orchestrated run's
:class:`~PycroFlow.live_analysis.frame_tap.FrameTap` — anything exposing the
``latest_frame`` display tap), skips unchanged frames, downsamples to a
view-sized image, overlays picasso-style detection boxes with the CURRENT
localize params, and pushes the payload on the service's seam
(:func:`~PycroFlow.live_analysis.client_seam.push_thumbnail`). Shared by the
preview session and the live-run coordinator so the Overview behaves
identically in both — previously each context re-implemented (or lacked)
its own pusher.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

#: Push cadence; a frame is only sent when it CHANGED since the last tick.
DEFAULT_INTERVAL_S = 0.5
#: Downsample thumbnails to roughly this edge length (full 2048² frames are
#: ~8 MB per push through the GUI).
DEFAULT_MAX_PX = 512


class ThumbnailStreamer:
    """Stream Overview thumbnails from a latest-frame provider.

    Parameters
    ----------
    service : LiveAnalysisService
        Supplies the hub and the run_id the pushes are tagged with.
    frame_provider : callable
        Zero-arg callable returning the newest frame (ndarray) or None.
    pixelsize_nm : float or None
        Sample-plane pixel size; scaled by the downsampling stride for the
        scale bar.
    params_provider : callable or None
        Zero-arg callable returning the CURRENT localize params dict
        (``Box Size`` / ``Min. Net Gradient``) for the box overlay — read
        per tick, so live tuning (the sidebar) takes effect immediately.
    with_boxes : bool
        False skips detection entirely (no overlay).
    interval_s, max_px : float, int
        Push cadence and thumbnail edge bound.
    """

    def __init__(
        self,
        service,
        frame_provider,
        *,
        pixelsize_nm=None,
        params_provider=None,
        with_boxes: bool = True,
        interval_s: float = DEFAULT_INTERVAL_S,
        max_px: int = DEFAULT_MAX_PX,
    ):
        self._service = service
        self._frame_provider = frame_provider
        self._pixelsize = pixelsize_nm
        self._params_provider = params_provider or (lambda: {})
        self._with_boxes = with_boxes
        self._interval_s = interval_s
        self._max_px = max(1, int(max_px))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "ThumbnailStreamer":
        self._thread = threading.Thread(
            target=self._run, name="live-thumbnails", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 10.0) -> bool:
        """Join the streamer thread (tests); True when it ended."""
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        from PycroFlow.live_analysis.boxes import identify_boxes
        from PycroFlow.live_analysis.client_seam import push_thumbnail

        last = None
        while not self._stop.is_set():
            try:
                frame = self._frame_provider()
            except Exception as exc:  # noqa: BLE001 - provider stays soft
                logger.warning("thumbnail frame provider: {!r}".format(exc))
                frame = None
            # Skip unchanged frames — no point re-rendering the same image.
            if frame is not None and frame is not last:
                last = frame
                stride = max(
                    1, int(-(-max(frame.shape) // self._max_px))
                )  # ceil div
                thumb = frame[::stride, ::stride]
                boxes = None
                box_size = 7
                if self._with_boxes:
                    params = self._params_provider() or {}
                    box_size = int(params.get("Box Size", 7))
                    # Identify on the FULL frame (downsampling destroys the
                    # PSF gradients), then scale onto the thumbnail.
                    boxes = identify_boxes(
                        frame,
                        box_size,
                        float(params.get("Min. Net Gradient", 5000)),
                    )
                    if boxes is not None and stride > 1:
                        boxes = [coord / stride for coord in boxes]
                push_thumbnail(
                    self._service.hub,
                    self._service.run_id,
                    thumb,
                    pixelsize_nm=(
                        self._pixelsize * stride
                        if self._pixelsize
                        else self._pixelsize
                    ),
                    boxes=boxes,
                    box_size=max(3, round(box_size / stride)),
                )
            time.sleep(self._interval_s)
