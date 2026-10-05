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

Enablement — live analysis runs only when ALL hold (else the run is
byte-identical to a pre-WP-LIVE-INT run):

* the imaging system carries the full picasso ``camera_info``
  (photon-conversion keys: Baseline/Sensitivity/Gain/Qe/Pixelsize) — the real
  :class:`~PycroFlow.imaging.ImagingSystem` via a ``camera_info`` block in
  its imaging config (setup YAML), the emulated one via its
  ``live_camera_info`` attribute;
* the ``PYCROFLOW_LIVE_ANALYSIS`` env var is not ``0`` / ``off`` / ``false``.

Tuning rides the same channels: a ``live_analysis`` dict in the imaging
config (or ``live_analysis_options`` attribute) may set ``localize_params``,
``batch_size``, ``n_workers``, ``queue_size``, ``use_processes``,
``first_frame_timeout_s``.

Laser interlock (T3/C21) in the orchestrated context: the service runs with
``interlock_per_fov=False`` — abort/error engage immediately, and
:meth:`stop_run` engages once at experiment end — because between rounds the
ILLUMINATION HANDLER owns the lasers and a concurrent per-clean-FOV all-off
would race its protocol entries.

Early-abort (initiative #3): a client's ``request_abort`` ends the CURRENT
FOV only — the coordinator relays the service's ``abort_requested`` to the
tap (ending the in-flight MDA, frames so far kept) and re-arms the service
before the next acquire step, so the protocol continues. The orchestrator
Abort (Run Sequence tab) is separate and also tears live analysis down.
"""

from __future__ import annotations

import os
import threading

from loguru import logger

from PycroFlow.services.registry import registry_client_from_env

_DISABLE_VALUES = ("0", "off", "false", "no")


def _env_disabled() -> bool:
    val = os.environ.get("PYCROFLOW_LIVE_ANALYSIS", "").strip().lower()
    return val in _DISABLE_VALUES and val != ""


def _camera_info_for(imaging_system) -> dict | None:
    """The picasso camera_info the fit needs, or None (live analysis off)."""
    info = getattr(imaging_system, "live_camera_info", None)
    if not info:
        config = getattr(imaging_system, "config", None) or {}
        info = config.get("camera_info") if isinstance(config, dict) else None
    return dict(info) if info else None


def _options_for(imaging_system) -> dict:
    opts = getattr(imaging_system, "live_analysis_options", None)
    if not opts:
        config = getattr(imaging_system, "config", None) or {}
        opts = (
            config.get("live_analysis") if isinstance(config, dict) else None
        )
    return dict(opts) if opts else {}


class LiveRunCoordinator:
    """Owns the live-analysis lifecycle of one orchestrated run at a time."""

    # Seam for tests: replace with a stub factory to inject a mock registry.
    registry_client_factory = staticmethod(registry_client_from_env)

    def __init__(self):
        self._service = None
        self._tap = None
        self._imaging = None
        self._worker: threading.Thread | None = None
        self._seam_client = None
        self._registry = None
        self._run_id: str | None = None
        self._experiment_id: str | None = None
        self._camera_info: dict = {}
        self._options: dict = {}

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
                "live analysis disabled: the imaging system carries no "
                "picasso camera_info (add a `camera_info` block to the "
                "setup's imaging config to enable it)"
            )
            return None

        from PycroFlow.live_analysis.client_seam import CallbackClient
        from PycroFlow.live_analysis.frame_tap import FrameTap
        from PycroFlow.live_analysis.service import LiveAnalysisService

        try:
            registry = self.registry_client_factory()
        except Exception as exc:  # noqa: BLE001 - factory must not stop us
            logger.warning("registry client factory raised: {!r}".format(exc))
            registry = None
        self._registry = registry
        self._camera_info = camera_info
        self._options = _options_for(imaging_system)
        self._service = LiveAnalysisService(
            registry_client=registry,
            illumination_system=illumination_system,
            interlock_per_fov=False,
        )
        self._run_id = self._service.start_experiment()
        self._experiment_id = self._log_experiment(design, setup_name)
        # Relay the seam's early-abort to the in-flight MDA (end this FOV).
        self._seam_client = CallbackClient(self._on_update)
        self._service.hub.add(self._seam_client)
        self._tap = FrameTap(on_fov_start=self._on_fov_start)
        self._imaging = imaging_system
        imaging_system.frame_tap = self._tap
        logger.info("live analysis attached (run_id {})".format(self._run_id))
        return self._run_id

    def stop_run(self, *, abort: bool = False) -> None:
        """Detach and tear down after a run (or before a new one). Idempotent."""
        service, tap, imaging = self._service, self._tap, self._imaging
        if service is None and tap is None:
            return
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
            self._join_worker(timeout=60.0)
            if service is not None:
                if self._seam_client is not None:
                    service.hub.remove(self._seam_client)
                service.shutdown(
                    reason="orchestrator abort" if abort else "experiment end"
                )
            close = getattr(self._registry, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "registry client close raised: {!r}".format(exc)
                    )
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            logger.warning("live analysis teardown raised: {!r}".format(exc))
        finally:
            self._service = None
            self._tap = None
            self._imaging = None
            self._seam_client = None
            self._registry = None
            self._experiment_id = None
            # _run_id intentionally kept: it identifies the finished run.

    # -- per-FOV plumbing -----------------------------------------------------

    def _on_fov_start(self, acq_name, acquisition_config, frame_q) -> None:
        """Tap callback (acquisition thread): consume this FOV's frames."""
        service = self._service
        if service is None:
            return
        # record_movie serializes FOVs, so the previous worker is normally
        # done; join defensively so two run_fov calls never overlap.
        self._join_worker(timeout=60.0)
        # Early-abort scope: an aborted FOV re-arms at its END (_run_fov), so
        # the flag being set HERE means the request arrived between FOVs —
        # apply it to this FOV (end its acquisition immediately) rather than
        # dropping it or letting acquisition and pipeline disagree.
        if service.abort_requested():
            tap = self._tap
            if tap is not None:
                tap.request_fov_end()

        from PycroFlow.live_analysis.frame_source import SOURCE_IMAGE_QUEUE
        from PycroFlow.live_analysis.service import (
            DEFAULT_LOCALIZE_PARAMS,
            FovConfig,
        )

        opts = self._options
        fov_fields = {}
        t_exp = (acquisition_config or {}).get("t_exp")
        if t_exp is not None:
            fov_fields["exposure_ms"] = t_exp
        acquisition_fields = {}
        if self._experiment_id is not None:
            acquisition_fields["experiment_id"] = self._experiment_id
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
            fov_fields=fov_fields,
            acquisition_fields=acquisition_fields,
            pixelsize_nm=self._camera_info.get("Pixelsize"),
        )
        self._worker = threading.Thread(
            target=self._run_fov,
            args=(service, cfg, acq_name),
            name="live-fov",
            daemon=True,
        )
        self._worker.start()

    def _run_fov(self, service, cfg, acq_name) -> None:
        try:
            result = service.run_fov(cfg)
            if result.aborted:
                # Per-FOV early-abort: this FOV ended (recorded honestly as
                # aborted, interlock engaged); re-arm so the protocol's next
                # acquire step runs a fresh, clean FOV.
                service.clear_abort()
            logger.info(
                "live analysis FOV {} done (frames_read={}, partial={})".format(
                    acq_name, result.frames_read, result.partial
                )
            )
        except Exception as exc:  # noqa: BLE001 - worker must never crash out
            logger.exception(
                "live analysis FOV {} failed: {!r}".format(acq_name, exc)
            )

    def _join_worker(self, timeout: float) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)
            if worker.is_alive():
                logger.warning(
                    "live analysis FOV worker still running after {}s".format(
                        timeout
                    )
                )
        self._worker = None

    # -- seam relay -----------------------------------------------------------

    def _on_update(self, update) -> None:
        """Hub client: turn ``abort_requested`` into end-this-FOV at the MDA."""
        tap = self._tap
        if (
            tap is not None
            and update.kind == "state"
            and update.payload.get("state") == "abort_requested"
        ):
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

        payload = build_experiment_payload(
            experiment_id=new_run_id(),
            run_id=self._run_id,
            design=design,
            setup_name=setup_name,
        )
        if self._registry is None:
            self._service.hub.push_kind(
                "record", self._run_id, payload=payload, posted=False
            )
            return None
        try:
            row = post_experiment_record(self._registry, payload)
            exp_id = row.get("id", payload["id"])
            self._service.hub.push_kind(
                "record",
                self._run_id,
                ids={"experiment_id": exp_id},
                posted=True,
            )
            return exp_id
        except Exception as exc:  # noqa: BLE001 - outage isn't fatal
            logger.warning(
                "posting experiment record failed: {!r}".format(exc)
            )
            self._service.hub.push_kind(
                "record", self._run_id, error=repr(exc), posted=False
            )
            return None
