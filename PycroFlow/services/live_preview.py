"""LivePreviewSession — live localization over Micro-Manager's Live preview.

The Live tab's PREVIEW mode: PycroFlow watches MM's Live view — starting
Live mode itself when it isn't running (and restoring the operator's state
when the preview ends: Live is only switched off if the preview switched it
on). A
:class:`~PycroFlow.live_analysis.frame_source.RamPeekFrameSource`
non-destructively peeks the newest frame off MM's circular buffer and a
:class:`~PycroFlow.live_analysis.service.LiveAnalysisService` localizes the
peeked stream, so the Live tab shows metrics + a thumbnail with NO protocol
running. Deliberately read-only and side-effect-free:

* **No registry** — a preview is not an experiment; records are surfaced on
  the hub (``posted=False``) but never posted.
* **No laser interlock** — the operator owns the illumination while
  previewing; stopping a preview must not darken their lasers
  (``illumination_system=None`` makes the T3 interlock a no-op).
* **LOSSY by design** — the peek skips frames under load (WP-4: view-only,
  never the authoritative reduction); metrics are indicative.

Lifecycle: :meth:`start` returns the service (the Live tab subscribes its
shell to it), a worker thread runs the pipeline, and a thumbnail thread
pushes new frames on the seam every ~0.5 s (downsampled to a view-sized
image, skipped when unchanged). The preview ends on :meth:`stop` (toggle,
run start, window close) OR on the shell's Early-abort — the service's
``abort_requested`` ends the preview outright; the Live tab reflects it on
the toggle. Stop never blocks the caller: the abort is requested first and
the peek source's poller is closed on a background thread (its join can ride
a slow ZMQ call); :meth:`wait` is the explicit join for tests. Enablement
mirrors the orchestrated path: the imaging config's ``camera_info`` block.
On an emulated setup (no MM core) the preview runs paced, looping
``MockFrameSource`` segments, so the mode is demoable and testable with no
instrument.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

from PycroFlow.services.imaging_config import (
    build_fov_config,
    camera_info_for,
    live_options_for,
)

_THUMB_INTERVAL_S = 0.5


def set_mm_live_mode(on) -> bool:
    """Switch Micro-Manager's Live mode, best-effort. Never raises.

    Builds its own ``Studio`` handle (pycromanager bridge objects are
    per-thread; this runs on the preview worker, mirroring the peek source's
    own ``Core``).

    Parameters
    ----------
    on : bool
        Desired Live-mode state.

    Returns
    -------
    bool
        True only when THIS call flipped the mode — the preview turns Live
        off at the end only if it was the one to turn it on, so an
        operator-started Live view is never yanked away.
    """
    try:
        from pycromanager import Studio

        live = Studio(convert_camel_case=True).live()
        if bool(live.is_live_mode_on()) == bool(on):
            return False
        live.set_live_mode_on(bool(on))
        logger.info("MM Live mode -> {}", "on" if on else "off")
        return True
    except Exception as exc:  # noqa: BLE001 - preview must not depend on it
        logger.warning("could not switch MM Live mode: {!r}", exc)
        return False


# Downsample preview thumbnails to roughly this edge length (full frames off
# a 2048² camera would push ~8 MB through the GUI twice a second for the
# whole — possibly hours-long — preview). Override via
# ``live_analysis: {preview_thumbnail_max_px: ...}``.
_THUMB_MAX_PX = 512


class LivePreviewSession:
    """One MM-live-preview watch at a time (start/stop from the Live tab)."""

    def __init__(self):
        self._service = None
        self._source = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._thumb: threading.Thread | None = None
        # The session's live_analysis options; its ``localize_params`` entry
        # is the SINGLE source of the detection params — the box overlay
        # reads it per thumbnail tick and each pipeline segment is built from
        # it, so the sidebar's Core controls steer both (set_overlay_params).
        self._options: dict = {}

    @property
    def service(self):
        """The preview's LiveAnalysisService, or None when not previewing."""
        return self._service

    @property
    def active(self) -> bool:
        return self._service is not None

    # -- lifecycle ------------------------------------------------------------

    def start(self, imaging_system):
        """Start watching MM's live preview. Never raises.

        Parameters
        ----------
        imaging_system : object or None
            The connected imaging system. A real
            :class:`~PycroFlow.imaging.ImagingSystem` (has an MM ``core``)
            is watched via the RAM peek; an emulated system runs paced
            synthetic segments instead.

        Returns
        -------
        LiveAnalysisService or None
            The service to subscribe the Live tab to, or None when preview
            is unavailable (no imaging system / no ``camera_info`` block).
        """
        try:
            return self._start(imaging_system)
        except Exception as exc:  # noqa: BLE001 - preview must never crash
            logger.warning("live preview could not start: {!r}".format(exc))
            self.stop()
            return None

    def _start(self, imaging_system):
        self.stop()
        if imaging_system is None:
            logger.info("live preview unavailable: no imaging system")
            return None
        camera_info = camera_info_for(imaging_system)
        if not camera_info:
            logger.info(
                "live preview unavailable: the imaging system's config "
                "carries no `camera_info` block"
            )
            return None
        options = live_options_for(imaging_system)

        from PycroFlow.live_analysis.service import (
            DEFAULT_LOCALIZE_PARAMS,
            LiveAnalysisService,
        )

        options.setdefault("localize_params", dict(DEFAULT_LOCALIZE_PARAMS))
        self._options = options

        # Read-only session: no registry, no illumination (interlock no-op).
        self._service = LiveAnalysisService()
        self._service.start_experiment()
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run,
            args=(self._service, imaging_system, camera_info, options),
            name="live-preview",
            daemon=True,
        )
        self._worker.start()
        self._thumb = threading.Thread(
            target=self._push_thumbnails,
            args=(self._service, camera_info, options),
            name="live-preview-thumb",
            daemon=True,
        )
        self._thumb.start()
        logger.info(
            "live preview started (run_id {})".format(self._service.run_id)
        )
        return self._service

    def stop(self) -> None:
        """End the preview. Never blocks the caller.

        The pipeline abort is requested first (fast — it unblocks the
        drain), then the peek source is closed on a background thread: its
        ``close()`` joins the poller, which can ride out a slow ZMQ call, and
        stop() runs on the GUI thread (toggle-off, run start, window close).
        :meth:`wait` is the explicit join for tests.
        """
        service, source = self._service, self._source
        self._service = None
        self._source = None
        self._stop.set()
        if service is not None:
            service.request_abort()
        if source is not None:
            threading.Thread(
                target=self._close_source,
                args=(source,),
                name="live-preview-close",
                daemon=True,
            ).start()
        if service is not None:
            logger.info("live preview stopped")

    @staticmethod
    def _close_source(source) -> None:
        try:
            source.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("preview source close raised: {!r}".format(exc))

    def wait(self, timeout: float = 30.0) -> bool:
        """Join the preview threads (tests); True when both ended.

        Parameters
        ----------
        timeout : float
            Per-thread join timeout in seconds.
        """
        done = True
        for thread in (self._worker, self._thumb):
            if thread is not None:
                thread.join(timeout)
                done = done and not thread.is_alive()
        return done

    def set_overlay_params(self, params) -> None:
        """Steer the detection params from the shell's Core controls.

        One source of truth: the merged values land in the session options'
        ``localize_params``, which the box overlay reads per thumbnail tick
        AND every pipeline segment is built from. A change during an active
        preview also RESTARTS the current segment (the source is closed; the
        worker loop re-attaches with the new values), so the metrics and the
        boxes never disagree for longer than one segment teardown. Safe to
        call whether or not a preview is active.

        Parameters
        ----------
        params : dict
            The sidebar payload — ``{"Box Size": int, "Min. Net Gradient":
            number, ...}``; unknown keys are ignored.
        """
        params = dict(params or {})
        merged = dict(self._options.get("localize_params") or {})
        changed = False
        for key in ("Box Size", "Min. Net Gradient"):
            if params.get(key) is not None and params[key] != merged.get(key):
                merged[key] = params[key]
                changed = True
        if not changed:
            return
        self._options["localize_params"] = merged
        # Nudge the active segment to re-attach with the new fit params
        # (same non-blocking close as stop(); the loop is NOT stopped, so it
        # starts a fresh segment — metrics restart under the new threshold).
        source = self._source
        if self.active and source is not None:
            threading.Thread(
                target=self._close_source,
                args=(source,),
                name="live-preview-param-restart",
                daemon=True,
            ).start()

    # -- pipeline -------------------------------------------------------------

    def _run(self, service, imaging_system, camera_info, options) -> None:
        from PycroFlow.live_analysis.frame_source import (
            MockFrameSource,
            RamPeekFrameSource,
        )

        # A real ImagingSystem carries the MM core -> RAM peek; an emulated
        # one runs paced synthetic segments (finite MockFrameSource at ~20
        # fps so an idle emulated preview doesn't pin the CPU).
        peek_mm = getattr(imaging_system, "core", None) is not None
        started_live = False
        try:
            segment = 0
            while not self._stop.is_set():
                if peek_mm:
                    # Drive MM's Live view for the operator: ensure it runs at
                    # every (re)attach — preview start and param restarts are
                    # both user-intent moments — and remember whether WE turned
                    # it on, so the preview's end restores MM to how the
                    # operator had it.
                    if set_mm_live_mode(True):
                        started_live = True
                    source = RamPeekFrameSource(
                        poll_s=options.get("preview_poll_s", 0.02),
                        port=options.get("preview_port", 4827),
                        camera_info=camera_info,
                    ).start()
                else:
                    source = MockFrameSource(
                        n_frames=120,
                        seed=segment,
                        produce_delay_s=options.get(
                            "preview_mock_delay_s", 0.05
                        ),
                    )
                self._source = source
                cfg = build_fov_config(
                    options,
                    camera_info=camera_info,
                    source_instance=source,
                    batch_size=20,
                    use_processes=False,
                )
                try:
                    service.run_fov(cfg)
                except Exception as exc:  # noqa: BLE001 - keep the GUI alive
                    logger.warning(
                        "live preview segment failed: {!r}".format(exc)
                    )
                    break
                finally:
                    try:
                        source.close()
                    except Exception:  # noqa: BLE001
                        pass
                if service.abort_requested():
                    # The shell's Early-abort during a preview ENDS the preview
                    # (the Live tab untoggles on the abort update).
                    self._stop.set()
                    break
                # Mock segments are finite; a peek segment also ends on a
                # param-change restart — loop re-attaches either way.
                segment += 1
        finally:
            if started_live:
                # The preview turned MM's Live mode on — restore the
                # operator's state on the way out (stop, Early-abort, or a
                # crashed segment).
                set_mm_live_mode(False)

    def _push_thumbnails(self, service, camera_info, options) -> None:
        from PycroFlow.live_analysis.boxes import identify_boxes
        from PycroFlow.live_analysis.client_seam import push_thumbnail

        pixelsize = camera_info.get("Pixelsize")
        max_px = options.get("preview_thumbnail_max_px", _THUMB_MAX_PX)
        with_boxes = bool(options.get("preview_boxes", True))

        last = None
        while not self._stop.is_set():
            source = self._source
            frame = getattr(source, "latest_frame", None)
            # Skip unchanged frames (MM Live off keeps latest_frame
            # identical) — no point re-rendering the same image.
            if frame is not None and frame is not last:
                last = frame
                # Detection-box overlay: identify on the FULL frame
                # (downsampling destroys the PSF gradients picasso detects
                # on) with the CURRENT localize params — the sidebar's Core
                # controls steer these live (set_overlay_params) — then
                # scale the centres onto the downsampled thumbnail.
                # `preview_boxes: false` turns the overlay off.
                params = self._options.get("localize_params") or {}
                box_size = int(params.get("Box Size", 7))
                min_ng = float(params.get("Min. Net Gradient", 5000))
                stride = max(
                    1, int(-(-max(frame.shape) // max_px))
                )  # ceil div
                thumb = frame[::stride, ::stride]
                boxes = (
                    identify_boxes(frame, box_size, min_ng)
                    if with_boxes
                    else None
                )
                if boxes is not None and stride > 1:
                    boxes = [coord / stride for coord in boxes]
                push_thumbnail(
                    service.hub,
                    service.run_id,
                    thumb,
                    pixelsize_nm=(
                        pixelsize * stride if pixelsize else pixelsize
                    ),
                    boxes=boxes,
                    box_size=max(3, round(box_size / stride)),
                )
            time.sleep(_THUMB_INTERVAL_S)
