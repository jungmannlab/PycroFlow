"""LiveRunCoordinator — live analysis driven by the orchestrated run.

WP-LIVE-INT: :class:`~PycroFlow.services.experiment_service.ExperimentService`
owns one coordinator. On run start it lazily builds the WP-4
:class:`~PycroFlow.live_analysis.service.LiveAnalysisService` over the
PRODUCTION acquisition — a
:class:`~PycroFlow.live_analysis.frame_tap.FrameTap` on the imaging system
feeds an ``ImageQueueFrameSource`` per acquire step — mints the experiment's
ULID run_id of record, auto-connects the registry from the environment
(one experiment-level record at run start + the WP-4 per-FOV records), and
tears everything down on run end / orchestrator abort. Every entry point is
best-effort: live analysis must never fail or stall a run, so errors are
logged, never raised.

Threading: the acquisition thread only ever ENQUEUES — ``_on_fov_start``
builds the FOV's config and puts it on a task queue that a single, long-lived
consumer thread (``_fov_loop_main``) drains, running the ``run_fov`` pipeline
strictly in order with no overlap. Teardown (:meth:`stop_run`) likewise does
only the fast, safety-relevant part synchronously (abort request, tap detach,
sentinels) and hands the joins/interlock/registry-close to a background
thread, so the GUI thread calling ``abort()``/``end()`` never blocks on a
draining pipeline (``wait_idle`` is the explicit block for tests/CLI).

Enablement — live analysis runs only when ALL hold (else the run is
byte-identical to a pre-WP-LIVE-INT run):

* the imaging system's ``config`` dict carries a ``camera_info`` block with
  the full picasso photon-conversion keys
  (Baseline/Sensitivity/Gain/Qe/Pixelsize) — the real
  :class:`~PycroFlow.imaging.ImagingSystem` gets it from the setup YAML's
  imaging section; the emulated system ships one built in;
* the ``PYCROFLOW_LIVE_ANALYSIS`` env var is not one of
  ``0`` / ``off`` / ``false`` / ``no``.

Tuning rides the same ``config['live_analysis']`` dict: ``localize_params``,
``batch_size``, ``n_workers``, ``queue_size``, ``use_processes``,
``first_frame_timeout_s``, ``max_pending_frames`` (the tap's RAM-backlog
bound), and ``archive_dir`` (enables the C15 raw-movie archive move per FOV;
unset = movies stay in the acquisition save_dir, logged once per run).

Laser interlock (T3/C21) in the orchestrated context: the service runs with
``interlock_per_fov=False`` — abort/error engage immediately, and teardown
engages once at experiment end — because between rounds the ILLUMINATION
HANDLER owns the lasers and a concurrent per-clean-FOV all-off would race its
protocol entries.

Early-abort (initiative #3): a client's ``request_abort`` ends ONE FOV. The
service counts requests in a monotonic generation; the relay (``_on_update``)
ends the in-flight MDA when one is active, otherwise the request stays armed
and ``_on_fov_start`` delivers it to the next FOV (generation-gated, so a
request already served to one acquisition is never replayed on the next). The
consumer loop re-arms via ``clear_abort(generation=...)`` — a request landing
after a FOV's fate was sealed survives and aborts the next FOV instead of
being lost. The orchestrator Abort (Run Sequence tab) is separate and also
tears live analysis down.
"""

from __future__ import annotations

import os
import queue
import threading

from loguru import logger

from PycroFlow.services.registry import registry_client_from_env

_DISABLE_VALUES = ("0", "off", "false", "no")


def _env_disabled() -> bool:
    val = os.environ.get("PYCROFLOW_LIVE_ANALYSIS", "").strip().lower()
    return val in _DISABLE_VALUES


def _imaging_config(imaging_system) -> dict:
    """The imaging system's config dict ({} when absent/not a dict)."""
    config = getattr(imaging_system, "config", None)
    return config if isinstance(config, dict) else {}


def _camera_info_for(imaging_system) -> dict | None:
    """The picasso camera_info the fit needs, or None (live analysis off)."""
    info = _imaging_config(imaging_system).get("camera_info")
    return dict(info) if info else None


def _options_for(imaging_system) -> dict:
    return dict(_imaging_config(imaging_system).get("live_analysis") or {})


class LiveRunCoordinator:
    """Owns the live-analysis lifecycle of one orchestrated run at a time."""

    # Seam for tests: replace with a stub factory to inject a mock registry.
    registry_client_factory = staticmethod(registry_client_from_env)

    def __init__(self):
        self._service = None
        self._tap = None
        self._imaging = None
        self._seam_client = None
        self._registry = None
        self._run_id: str | None = None
        self._experiment_id: str | None = None
        self._camera_info: dict = {}
        self._options: dict = {}
        # Per-FOV pipeline: the acquisition thread enqueues (cfg, name)
        # tasks; one consumer thread runs them in order (see module
        # docstring). _last_cfg is the most recently enqueued config — the
        # FOV whose acquisition ends next — stamped with the dataset path at
        # end-of-FOV (both touched only on the acquisition thread).
        self._fov_tasks: queue.Queue | None = None
        self._fov_loop: threading.Thread | None = None
        self._last_cfg = None
        self._teardown: threading.Thread | None = None
        # Newest abort generation whose acquisition-side effect (ending an
        # MDA) has been delivered; gates delivery so one request ends at
        # most one acquisition (see _on_update / _on_fov_start).
        self._acq_consumed_gen = 0

    # -- introspection (frontends) ------------------------------------------

    @property
    def service(self):
        """The live LiveAnalysisService, or None outside a run."""
        return self._service

    @property
    def run_id(self) -> str | None:
        return self._run_id

    # -- lifecycle (called by ExperimentService; never raises) ---------------

    def start_run(
        self,
        *,
        imaging_system,
        illumination_system=None,
        design=None,
        setup_name=None,
    ) -> str | None:
        """Attach live analysis to a starting run. Returns the run_id or None.

        None means live analysis is disabled for this run (no imaging system,
        no camera_info, or the env kill switch) — the run proceeds unchanged.
        """
        try:
            return self._start_run(
                imaging_system=imaging_system,
                illumination_system=illumination_system,
                design=design,
                setup_name=setup_name,
            )
        except Exception as exc:  # noqa: BLE001 - never block a run
            logger.warning("live analysis could not start: {!r}".format(exc))
            self.stop_run()
            return None

    def _start_run(
        self, *, imaging_system, illumination_system, design, setup_name
    ) -> str | None:
        self.stop_run()  # a previous run's leftovers, defensively
        if imaging_system is None:
            return None
        if _env_disabled():
            logger.info("live analysis disabled via PYCROFLOW_LIVE_ANALYSIS")
            return None
        camera_info = _camera_info_for(imaging_system)
        if not camera_info:
            logger.info(
                "live analysis disabled: the imaging system's config carries "
                "no `camera_info` block (add one to the setup's imaging "
                "config to enable it)"
            )
            return None

        from PycroFlow.live_analysis.client_seam import CallbackClient
        from PycroFlow.live_analysis.frame_tap import (
            DEFAULT_MAX_PENDING_FRAMES,
            FrameTap,
        )
        from PycroFlow.live_analysis.service import LiveAnalysisService

        try:
            registry = self.registry_client_factory()
        except Exception as exc:  # noqa: BLE001 - factory must not stop us
            logger.warning("registry client factory raised: {!r}".format(exc))
            registry = None
        self._registry = registry
        self._camera_info = camera_info
        self._options = _options_for(imaging_system)
        if not self._options.get("archive_dir"):
            logger.info(
                "live analysis: no live_analysis.archive_dir configured — "
                "raw movies stay in the acquisition save_dir (C15 archive "
                "move off for this run)"
            )
        self._service = LiveAnalysisService(
            registry_client=registry,
            illumination_system=illumination_system,
            interlock_per_fov=False,
        )
        self._run_id = self._service.start_experiment()
        self._acq_consumed_gen = self._service.abort_generation()
        self._experiment_id = self._log_experiment(design, setup_name)
        # One consumer for the whole run: FOVs never overlap, and the
        # acquisition thread never joins anything.
        self._fov_tasks = queue.Queue()
        self._fov_loop = threading.Thread(
            target=self._fov_loop_main,
            args=(self._service, self._fov_tasks),
            name="live-fov-loop",
            daemon=True,
        )
        self._fov_loop.start()
        # Relay the seam's early-abort to the in-flight MDA (end this FOV).
        self._seam_client = CallbackClient(self._on_update)
        self._service.hub.add(self._seam_client)
        self._tap = FrameTap(
            on_fov_start=self._on_fov_start,
            on_fov_end=self._on_fov_end,
            max_pending_frames=self._options.get(
                "max_pending_frames", DEFAULT_MAX_PENDING_FRAMES
            ),
        )
        self._imaging = imaging_system
        imaging_system.frame_tap = self._tap
        logger.info("live analysis attached (run_id {})".format(self._run_id))
        return self._run_id

    def stop_run(self, *, abort: bool = False, wait: bool = False) -> None:
        """Detach after a run (or before a new one). Idempotent.

        The fast, safety-relevant part (abort request, tap detach, loop
        sentinel) runs synchronously; the joins, the end-of-run laser
        interlock, and the registry close run on a background teardown
        thread so a GUI-thread caller never blocks on a draining pipeline.

        Parameters
        ----------
        abort : bool
            True aborts the in-flight FOV pipeline (orchestrator abort).
        wait : bool
            True blocks until the teardown finished (tests / CLI shutdown);
            see :meth:`wait_idle`.
        """
        service, tap, imaging = self._service, self._tap, self._imaging
        seam, registry = self._seam_client, self._registry
        tasks, loop = self._fov_tasks, self._fov_loop
        if service is None and tap is None:
            if wait:
                self.wait_idle()
            return
        self._service = None
        self._tap = None
        self._imaging = None
        self._seam_client = None
        self._registry = None
        self._experiment_id = None
        self._fov_tasks = None
        self._fov_loop = None
        self._last_cfg = None
        # _run_id intentionally kept: it identifies the finished run.
        try:
            if abort and service is not None:
                service.request_abort()
            if (
                imaging is not None
                and getattr(imaging, "frame_tap", None) is tap
            ):
                imaging.frame_tap = None
            if tap is not None:
                tap.end_fov()  # unblock a reader waiting on the sentinel
            if tasks is not None:
                tasks.put(None)  # consumer-loop exit sentinel
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            logger.warning("live analysis detach raised: {!r}".format(exc))
        self._teardown = threading.Thread(
            target=self._teardown_main,
            args=(service, seam, registry, loop, abort),
            name="live-teardown",
            daemon=True,
        )
        self._teardown.start()
        if wait:
            self.wait_idle()

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """Block until the last teardown finished; True when idle.

        For tests and CLI shutdown — GUI callers rely on the background
        teardown instead.
        """
        teardown = self._teardown
        if teardown is None:
            return True
        teardown.join(timeout)
        return not teardown.is_alive()

    def _teardown_main(self, service, seam, registry, loop, abort) -> None:
        try:
            if loop is not None:
                loop.join(120.0)
                if loop.is_alive():
                    logger.warning(
                        "live analysis FOV loop still draining at teardown"
                    )
            if service is not None:
                if seam is not None:
                    service.hub.remove(seam)
                service.shutdown(
                    reason="orchestrator abort" if abort else "experiment end"
                )
            close = getattr(registry, "close", None)
            if callable(close):
                close()
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            logger.warning("live analysis teardown raised: {!r}".format(exc))

    # -- per-FOV plumbing -----------------------------------------------------

    def _on_fov_start(self, acq_name, acquisition_config, frame_q) -> None:
        """Tap callback (acquisition thread): enqueue this FOV for the loop.

        Never blocks or joins — the acquisition thread only pays the queue
        put; the consumer loop runs the pipeline.
        """
        service, tasks = self._service, self._fov_tasks
        if service is None or tasks is None:
            return
        # A pending abort not yet served to any acquisition applies to this
        # FOV: end its MDA immediately. Generation-gated so a request that
        # already ended an earlier MDA is not replayed here (the pipeline
        # side re-arms separately via clear_abort in the consumer loop).
        gen = service.abort_generation()
        if service.abort_requested() and gen > self._acq_consumed_gen:
            self._acq_consumed_gen = gen
            tap = self._tap
            if tap is not None:
                tap.request_fov_end()

        from PycroFlow.live_analysis.frame_source import SOURCE_IMAGE_QUEUE
        from PycroFlow.live_analysis.service import (
            DEFAULT_LOCALIZE_PARAMS,
            FovConfig,
        )

        opts = self._options
        acquisition_config = acquisition_config or {}
        fov_fields = {}
        t_exp = acquisition_config.get("t_exp")
        if t_exp is not None:
            fov_fields["exposure_ms"] = t_exp
        acquisition_fields = {}
        if self._experiment_id is not None:
            acquisition_fields["experiment_id"] = self._experiment_id
        save_dir = acquisition_config.get("save_dir")
        if save_dir:
            acquisition_fields["raw_data_path"] = save_dir
        cfg = FovConfig(
            source_kind=SOURCE_IMAGE_QUEUE,
            source_kwargs={
                "frame_q": frame_q,
                "camera_info": dict(self._camera_info),
                "first_frame_timeout_s": opts.get(
                    "first_frame_timeout_s", 120.0
                ),
            },
            localize_params=dict(
                opts.get("localize_params", DEFAULT_LOCALIZE_PARAMS)
            ),
            batch_size=opts.get("batch_size", 100),
            n_workers=opts.get("n_workers", 2),
            queue_size=opts.get("queue_size", 8),
            use_processes=opts.get("use_processes", True),
            # movie_source_path is stamped at end-of-FOV (_on_fov_end) once
            # the actual dataset dir is known; the archive move runs only
            # when the setup configures a destination.
            movie_source_path=None,
            archive_dir=opts.get("archive_dir"),
            fov_fields=fov_fields,
            acquisition_fields=acquisition_fields,
            pixelsize_nm=self._camera_info.get("Pixelsize"),
        )
        self._last_cfg = cfg
        tasks.put((cfg, acq_name))

    def _on_fov_end(self) -> None:
        """Tap callback (acquisition thread), just before the sentinel.

        Stamps the finished dataset's on-disk path onto the FOV's config so
        the pipeline's archive step (which runs after the drain) can move the
        raw movie — best-effort; None simply skips the move.
        """
        cfg, imaging = self._last_cfg, self._imaging
        if cfg is not None and imaging is not None:
            cfg.movie_source_path = getattr(imaging, "last_dataset_path", None)

    def _fov_loop_main(self, service, tasks) -> None:
        """Single consumer: FOVs run strictly in order, never overlapping,
        and never on the acquisition thread."""
        while True:
            task = tasks.get()
            if task is None:
                return
            cfg, acq_name = task
            try:
                result = service.run_fov(cfg)
                if result.aborted:
                    # Re-arm ONLY the requests this FOV consumed; one that
                    # arrived after its fate was sealed stays armed and
                    # aborts the next FOV instead of being lost.
                    service.clear_abort(generation=result.abort_generation)
                logger.info(
                    "live analysis FOV {} done (frames_read={}, "
                    "partial={})".format(
                        acq_name, result.frames_read, result.partial
                    )
                )
            except Exception as exc:  # noqa: BLE001 - loop must survive
                logger.exception(
                    "live analysis FOV {} failed: {!r}".format(acq_name, exc)
                )

    # -- seam relay -----------------------------------------------------------

    def _on_update(self, update) -> None:
        """Hub client: deliver ``abort_requested`` to the acquisition side.

        An in-flight MDA is ended now (and the request marked served);
        otherwise the request stays armed and ``_on_fov_start`` serves it to
        the next FOV.
        """
        if (
            update.kind != "state"
            or update.payload.get("state") != "abort_requested"
        ):
            return
        service, tap = self._service, self._tap
        if service is None or tap is None:
            return
        if tap.fov_active():
            self._acq_consumed_gen = service.abort_generation()
            tap.request_fov_end()

    # -- registry: the experiment-level record --------------------------------

    def _log_experiment(self, design, setup_name) -> str | None:
        """Write the experiment row linking this run's records. Never raises.

        Returns the experiment id when posted, else None (per-FOV records
        then simply carry no ``experiment_id``, so a disabled registry never
        produces a dangling FK).
        """
        from PycroFlow.live_analysis.registry_payload import (
            build_experiment_payload,
            post_experiment_record,
        )
        from PycroFlow.live_analysis.run_id import new_run_id
        from PycroFlow.live_analysis.service import surface_record

        payload = build_experiment_payload(
            experiment_id=new_run_id(),
            run_id=self._run_id,
            design=design,
            setup_name=setup_name,
        )

        def _poster(client, p):
            row = post_experiment_record(client, p)
            return {"experiment_id": (row or {}).get("id") or p["id"]}

        ids = surface_record(
            self._service.hub, self._run_id, self._registry, payload, _poster
        )
        return ids.get("experiment_id") if ids else None
