"""LivePreviewSession — live localization over Micro-Manager's Live preview.

The Live tab's PREVIEW mode: the operator starts MM's own Live view (or any
running acquisition) and PycroFlow watches it — a
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
shell to it), a worker thread runs the pipeline until :meth:`stop`, and a
thumbnail thread pushes the newest frame on the seam every ~0.5 s. Stop is
non-blocking (daemon threads drain on their own); :meth:`wait` is the
explicit join for tests. Enablement mirrors the orchestrated path: the
imaging system's ``config['camera_info']`` block (localization needs the
photon-conversion keys). On an emulated setup (no MM core) the preview runs
on looping ``MockFrameSource`` segments, so the mode is demoable and testable
with no instrument.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

from PycroFlow.services.live_run import _camera_info_for, _options_for

_THUMB_INTERVAL_S = 0.5


class LivePreviewSession:
    """One MM-live-preview watch at a time (start/stop from the Live tab)."""

    def __init__(self):
        self._service = None
        self._source = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._thumb: threading.Thread | None = None

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
            is watched via the RAM peek; an emulated system runs looping
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
        camera_info = _camera_info_for(imaging_system)
        if not camera_info:
            logger.info(
                "live preview unavailable: the imaging system's config "
                "carries no `camera_info` block"
            )
            return None
        options = _options_for(imaging_system)

        from PycroFlow.live_analysis.service import LiveAnalysisService

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
            args=(self._service, camera_info),
            name="live-preview-thumb",
            daemon=True,
        )
        self._thumb.start()
        logger.info(
            "live preview started (run_id {})".format(self._service.run_id)
        )
        return self._service

    def stop(self) -> None:
        """End the preview. Non-blocking; the daemon threads drain on their
        own (:meth:`wait` is the explicit join for tests)."""
        service, source = self._service, self._source
        self._service = None
        self._source = None
        self._stop.set()
        if source is not None:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass
        if service is not None:
            service.request_abort()
            logger.info("live preview stopped")

    def wait(self, timeout: float = 30.0) -> bool:
        """Join the preview threads (tests); True when both ended."""
        done = True
        for thread in (self._worker, self._thumb):
            if thread is not None:
                thread.join(timeout)
                done = done and not thread.is_alive()
        return done

    # -- pipeline -------------------------------------------------------------

    def _run(self, service, imaging_system, camera_info, options) -> None:
        from PycroFlow.live_analysis.frame_source import (
            SOURCE_INSTANCE,
            MockFrameSource,
            RamPeekFrameSource,
        )
        from PycroFlow.live_analysis.service import (
            DEFAULT_LOCALIZE_PARAMS,
            FovConfig,
        )

        # A real ImagingSystem carries the MM core -> RAM peek; an emulated
        # one runs looping synthetic segments (finite MockFrameSource).
        peek_mm = getattr(imaging_system, "core", None) is not None
        segment = 0
        while not self._stop.is_set():
            if peek_mm:
                source = RamPeekFrameSource(
                    poll_s=options.get("preview_poll_s", 0.02),
                    port=options.get("preview_port", 4827),
                    camera_info=camera_info,
                ).start()
            else:
                source = MockFrameSource(n_frames=120, seed=segment)
            self._source = source
            cfg = FovConfig(
                source_kind=SOURCE_INSTANCE,
                source_kwargs={"source": source},
                localize_params=dict(
                    options.get("localize_params", DEFAULT_LOCALIZE_PARAMS)
                ),
                batch_size=options.get("batch_size", 20),
                n_workers=options.get("n_workers", 2),
                queue_size=options.get("queue_size", 8),
                use_processes=options.get("use_processes", False),
                pixelsize_nm=camera_info.get("Pixelsize"),
            )
            try:
                service.run_fov(cfg)
            except Exception as exc:  # noqa: BLE001 - keep the GUI alive
                logger.warning("live preview segment failed: {!r}".format(exc))
                break
            finally:
                try:
                    source.close()
                except Exception:  # noqa: BLE001
                    pass
            # The peek segment only ends on stop/abort; mock segments are
            # finite — re-arm and loop so the preview keeps animating.
            service.clear_abort()
            segment += 1
            if peek_mm and not self._stop.is_set():
                # A peek segment ending without stop means MM has no (new)
                # frames (Live turned off) — idle briefly, then re-attach.
                time.sleep(1.0)

    def _push_thumbnails(self, service, camera_info) -> None:
        pixelsize = camera_info.get("Pixelsize")
        while not self._stop.is_set():
            source = self._source
            frame = getattr(source, "latest_frame", None)
            if frame is not None:
                try:
                    service.hub.push_kind(
                        "thumbnail",
                        service.run_id,
                        data=frame.tobytes(),
                        shape=frame.shape,
                        dtype=str(frame.dtype),
                        pixelsize_nm=pixelsize,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "preview thumbnail push failed: {!r}".format(exc)
                    )
            time.sleep(_THUMB_INTERVAL_S)
