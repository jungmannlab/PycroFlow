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

from loguru import logger

from PycroFlow.services.imaging_config import (
    build_fov_config,
    camera_info_for,
    live_options_for,
)


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


class LivePreviewSession:
    """One MM-live-preview watch at a time (start/stop from the Live tab)."""

    def __init__(self):
        self._service = None
        self._source = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._thumb = None  # the shared ThumbnailStreamer
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
        from PycroFlow.live_analysis.thumbnails import ThumbnailStreamer

        self._thumb = ThumbnailStreamer(
            self._service,
            lambda: getattr(self._source, "latest_frame", None),
            pixelsize_nm=camera_info.get("Pixelsize"),
            params_provider=lambda: self._options.get("localize_params"),
            with_boxes=bool(options.get("preview_boxes", True)),
            max_px=options.get("preview_thumbnail_max_px", 512),
        ).start()
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
        service, source, thumb = self._service, self._source, self._thumb
        self._service = None
        self._source = None
        self._thumb = None
        self._stop.set()
        if thumb is not None:
            thumb.stop()
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
        if self._worker is not None:
            self._worker.join(timeout)
            done = done and not self._worker.is_alive()
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
