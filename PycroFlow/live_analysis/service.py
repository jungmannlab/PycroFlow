"""LiveAnalysisService — the headless server owning acquisition + the pipeline.

The first end-to-end slice: acquire one FOV -> live-localize (no drift) ->
metrics to the Quality tab -> one record to the registry. It is **frontend-
agnostic** (a headless server) and pushes state to clients only over the thin
:mod:`client_seam` boundary, so a Qt tab today and a WebSocket client later are
both just clients. It **never blocks acquisition**: frames are read off disk (a
separate-process tail reader by default), a pool of worker processes localizes
them off a bounded queue, and if the pool can't keep pace the pipeline LAGS
(never subsamples the authoritative stream).

Per-FOV flow (:meth:`run_fov`):
  1. spawn the worker pool + compute backend;
  2. iterate the frame source's contiguous, position-pure batches, submitting
     each to the backend (lagging under backpressure);
  3. a drain thread folds each batch's locs into :class:`RunningMetrics` and
     pushes a metrics update to clients;
  4. on normal end / early-abort / ANY exception, a ``try/finally`` engages the
     laser interlock (T3, C21), snapshots the final metrics, archives the raw
     movie (fallback path), and posts the per-FOV registry record;
  5. tear the pool down.

The experiment-level entry (:meth:`start_experiment`) mints the ULID ``run_id``
once and tags every FOV/record with it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from loguru import logger

from PycroFlow.live_analysis.archive import (
    WRITE_TARGET_LOCAL,
    WRITE_TARGET_POOL,
    archive_movie,
)
from PycroFlow.live_analysis.client_seam import UpdateHub
from PycroFlow.live_analysis.compute_backend import (
    COMPUTE_LOCAL,
    POLICY_LAG,
    make_compute_backend,
)
from PycroFlow.live_analysis.laser_interlock import LaserInterlock
from PycroFlow.live_analysis.metrics import RunningMetrics
from PycroFlow.live_analysis.registry_payload import (
    build_fov_payload,
    post_fov_record,
)
from PycroFlow.live_analysis.run_id import new_run_id
from PycroFlow.live_analysis.worker import LocalizeRequest, LocalizeResult

# Default picasso identification params — placeholders until the recommender
# (WP-3/WP-7) supplies sample-aware values; a caller overrides per FOV.
DEFAULT_LOCALIZE_PARAMS = {
    "Box Size": 7,
    "Min. Net Gradient": 5000,
}


def surface_record(hub, run_id, registry, payload, poster):
    """Post one registry record best-effort and surface the outcome on a hub.

    The single implementation of the record-surfacing triad every registry
    writer uses (the per-FOV records here, the experiment-level record in
    the live-run coordinator), so all ``kind == "record"`` updates share one
    shape: ``posted=True`` with ``ids``, or ``posted=False`` with the unposted
    ``payload`` (no client) / the ``error`` (post failed). Never raises; a
    registry outage is never fatal.

    Parameters
    ----------
    hub : UpdateHub
        Where the outcome is pushed (``kind="record"``).
    run_id : str
        The experiment run_id the update is tagged with.
    registry : object or None
        The registry client; None surfaces the payload as not posted.
    payload : dict
        The record payload (also surfaced verbatim when not posted).
    poster : callable
        ``poster(registry, payload) -> dict`` returning the created ids.

    Returns
    -------
    dict or None
        The created ids, or None when not posted.
    """
    if registry is None:
        hub.push_kind("record", run_id, payload=payload, posted=False)
        return None
    try:
        ids = poster(registry, payload)
        hub.push_kind("record", run_id, ids=ids, posted=True)
        return ids
    except Exception as exc:  # noqa: BLE001 - a registry outage isn't fatal
        logger.warning("posting record failed: {!r}".format(exc))
        hub.push_kind("record", run_id, error=repr(exc), posted=False)
        return None


@dataclass
class FovConfig:
    """Per-FOV inputs (what/where to read + how to localize + where to archive)."""

    source_kind: str = "tiff-tail"
    source_kwargs: dict = field(default_factory=dict)
    localize_params: dict = field(
        default_factory=lambda: dict(DEFAULT_LOCALIZE_PARAMS)
    )
    fitting_method: str = "gausslq"
    batch_size: int = 100
    n_workers: int = 2
    queue_size: int = 8
    compute_kind: str = COMPUTE_LOCAL
    use_processes: bool = True
    # Movie write-target + archive (fallback path only).
    write_target: str = WRITE_TARGET_LOCAL
    movie_source_path: str | None = None
    archive_dir: str | None = None
    # Registry row extras (positions, rate, etc.).
    fov_fields: dict = field(default_factory=dict)
    acquisition_fields: dict = field(default_factory=dict)
    analysis_fields: dict = field(default_factory=dict)
    pixelsize_nm: float | None = None
    # Metric snapshot cadence pushed to clients (s).
    metrics_push_interval_s: float = 0.5


@dataclass
class FovResult:
    """Outcome of one FOV run (returned + logged; drives the golden test)."""

    run_id: str
    metrics: dict
    frames_read: int
    backend_stats: dict
    interlock: object
    archive: object
    registry_ids: dict | None
    aborted: bool
    error: str | None
    # Coverage provenance: frames_read vs frames_localized + partial flags, so
    # partial coverage (abort/error/dropped) is explicit, never silent.
    coverage: dict = field(default_factory=dict)
    # The abort generation observed when this FOV's fate was sealed; lets an
    # orchestrated caller re-arm exactly the aborts this FOV consumed (see
    # clear_abort) without erasing a newer, not-yet-served request.
    abort_generation: int = 0

    @property
    def partial(self) -> bool:
        """True when the FOV did not localize every frame it read."""
        return bool(self.coverage.get("partial", False))


class LiveAnalysisService:
    """Headless owner of live acquisition + the frames->locs pipeline.

    Parameters
    ----------
    registry_client : object or None
        A picasso-registry client (real or the in-memory mock). When None, the
        per-FOV record is not posted (records are still built + surfaced).
    illumination_system : object or None
        The illumination system for the laser interlock (None -> interlock no-op).
    lasers_off_finally : bool
        Master switch for the T3 interlock (default ON, fail-safe).
    interlock_per_fov : bool
        When True (default — the standalone WP-4 behaviour, where one FOV is
        effectively the run), the interlock engages on EVERY FOV end. The
        orchestrated path (WP-LIVE-INT) passes False: between rounds the
        ILLUMINATION HANDLER owns the lasers (its protocol entries set the
        non-acquisition power), so a clean-FOV all-off would race it. Abort
        and error still engage immediately regardless, and :meth:`shutdown`
        provides the matching end-of-experiment engage (C21's
        "end-of-run/on-abort" scope).
    """

    def __init__(
        self,
        *,
        registry_client=None,
        illumination_system=None,
        lasers_off_finally: bool = True,
        interlock_per_fov: bool = True,
    ):
        self._registry = registry_client
        self._interlock = LaserInterlock(
            illumination_system, enabled=lasers_off_finally
        )
        self._interlock_per_fov = interlock_per_fov
        self.hub = UpdateHub()
        self._run_id: str | None = None
        self._abort = threading.Event()
        # Monotonic count of abort REQUESTS, so a per-FOV caller can clear
        # exactly the requests a finished FOV consumed (clear_abort) without
        # losing one that arrived after the FOV's fate was sealed.
        self._abort_gen = 0
        self._abort_lock = threading.Lock()

    # -- experiment lifecycle ----------------------------------------------
    def start_experiment(self, run_id: str | None = None) -> str:
        """Mint (or accept) the ULID ``run_id`` for this experiment.

        Returns the run_id; every FOV/record in the experiment is tagged with it.
        """
        self._run_id = run_id or new_run_id()
        self._abort.clear()
        logger.info(
            "live-analysis experiment run_id = {}".format(self._run_id)
        )
        self.hub.push_kind("state", self._run_id, state="experiment_started")
        return self._run_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    def request_abort(self) -> None:
        """Early-abort control call (from a client / QC). Non-blocking.

        Sets the abort flag; the running FOV stops reading new batches, drains
        what's in flight, and the ``finally`` engages the laser interlock.
        """
        logger.warning("live-analysis early-abort requested")
        with self._abort_lock:
            self._abort_gen += 1
            self._abort.set()
        self.hub.push_kind("state", self._run_id, state="abort_requested")

    def abort_requested(self) -> bool:
        """True while the early-abort flag is armed (see :meth:`clear_abort`)."""
        return self._abort.is_set()

    def abort_generation(self) -> int:
        """Monotonic count of abort requests so far (see :meth:`clear_abort`)."""
        with self._abort_lock:
            return self._abort_gen

    def clear_abort(self, generation: int | None = None) -> None:
        """Re-arm after a per-FOV early-abort (orchestrated runs).

        The standalone WP-4 flow treats an early-abort as ending the
        experiment, so the flag only clears on :meth:`start_experiment`. In an
        ORCHESTRATED run the abort's scope is ONE FOV (initiative #3): the
        live-run coordinator clears the flag at the END of an aborted FOV so
        the protocol continues with a fresh one.

        Parameters
        ----------
        generation : int or None
            When given (``FovResult.abort_generation``), the flag clears ONLY
            if no newer abort arrived since that FOV's fate was sealed — a
            request landing in the gap stays armed and aborts the next FOV
            instead of being silently lost. None clears unconditionally.
        """
        with self._abort_lock:
            if generation is None or generation == self._abort_gen:
                self._abort.clear()

    def shutdown(self, *, reason: str = "experiment end"):
        """End-of-experiment safety engage of the laser interlock. Never raises.

        The orchestrated path suppresses the per-clean-FOV engage
        (``interlock_per_fov=False``); this is the matching end-of-run engage
        (C21). Idempotent — engaging an already-dark system is a no-op.
        """
        result = None
        try:
            result = self._interlock.engage(reason=reason)
        except Exception as exc:  # noqa: BLE001 - interlock must never raise
            logger.error("shutdown interlock raised: {!r}".format(exc))
        self.hub.push_kind("state", self._run_id, state="experiment_ended")
        return result

    # -- per-FOV run --------------------------------------------------------
    def run_fov(self, cfg: FovConfig) -> FovResult:
        """Run the full live-analysis slice for one FOV. Never blocks acquisition.

        The whole body is under ``try/finally`` so the laser interlock fires on
        normal end, early-abort, AND any exception — the T3 fail-safe (C21).
        """
        if self._run_id is None:
            self.start_experiment()
        run_id = self._run_id
        assert run_id is not None

        from PycroFlow.live_analysis.frame_source import make_frame_source

        metrics = RunningMetrics()
        metrics.set_pixelsize_nm(cfg.pixelsize_nm)
        source = make_frame_source(cfg.source_kind, **cfg.source_kwargs)

        aborted = False
        error: str | None = None
        backend = None
        interlock_result = None
        archive_result = None
        registry_ids = None

        def _on_result(res: LocalizeResult) -> None:
            # Drain-thread sink: fold locs into the running metrics and push.
            if res.locs is None:
                return
            metrics.update(res.locs, res.n_frames)

        try:
            info = source.camera_info()
            if info is not None:
                metrics.set_info(info)
            backend = make_compute_backend(
                cfg.compute_kind,
                info=info,
                params=cfg.localize_params,
                on_result=_on_result,
                n_workers=cfg.n_workers,
                queue_size=cfg.queue_size,
                policy=POLICY_LAG,
                fitting_method=cfg.fitting_method,
                use_processes=cfg.use_processes,
            )
            backend.start()
            self.hub.push_kind("state", run_id, state="fov_started")

            seq = 0
            last_push = 0.0
            for batch in source.batches(cfg.batch_size):
                if self._abort.is_set():
                    aborted = True
                    break
                # Lazily learn camera info if the source only knew it after open.
                if info is None:
                    info = source.camera_info()
                    if info is not None:
                        metrics.set_info(info)
                ok = backend.submit(
                    LocalizeRequest(
                        seq=seq,
                        frames=batch.frames,
                        start_frame=batch.start_frame,
                        position=batch.position,
                    )
                )
                if not ok:
                    # Backend is stopping (abort); stop feeding it.
                    aborted = self._abort.is_set()
                    break
                seq += 1
                now = time.monotonic()
                if now - last_push >= cfg.metrics_push_interval_s:
                    self._push_metrics(run_id, metrics, backend)
                    last_push = now

            # The source can end (sentinel) before the per-batch abort check
            # runs again — e.g. an early-abort that already stopped the
            # producer, with the last frames still in flight. Record the
            # abort honestly instead of reporting a clean FOV the operator
            # in fact aborted (coverage still shows what was localized).
            if self._abort.is_set():
                aborted = True

        except (
            Exception
        ) as exc:  # noqa: BLE001 - captured; interlock still runs
            error = repr(exc)
            logger.exception("live-analysis FOV failed")
        finally:
            # Seal the abort bookkeeping for this FOV: requests up to here
            # shaped its fate; anything newer belongs to the next FOV (the
            # caller passes this to clear_abort so a late request survives).
            with self._abort_lock:
                abort_generation = self._abort_gen
            # --- T3 laser fail-safe (C21): abort and error ALWAYS engage;
            # a clean FOV end engages only in per-FOV mode (standalone WP-4).
            # The orchestrated path engages once at experiment end instead
            # (see ``interlock_per_fov`` / :meth:`shutdown`).
            if self._interlock_per_fov or aborted or error:
                try:
                    interlock_result = self._interlock.engage(
                        reason=(
                            "fov end"
                            if not (aborted or error)
                            else ("abort" if aborted else "error")
                        )
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - interlock must never raise
                    logger.error("interlock itself raised: {!r}".format(exc))

            # Drain in-flight localizations + tear the pool down.
            if backend is not None:
                try:
                    backend.drain_and_stop(timeout=60.0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("backend teardown raised: {!r}".format(exc))
            try:
                source.close()
            except Exception:
                pass

            frames_read = source.frames_read()
            backend_stats = backend.stats() if backend is not None else {}
            final_metrics = metrics.snapshot(force_nena=True)

            # --- No-subsample reconciliation (acquisition integrity) ---
            # frames_read = frames the source EMITTED (and were counted read);
            # localized = frames actually folded into the authoritative metrics.
            # On a CLEAN finish every read frame must have been localized — the
            # "no subsampling" guarantee, enforced here at runtime (not just in
            # tests, and not a bare ``assert`` that ``python -O`` would strip).
            # On abort/error partial coverage is expected and recorded honestly.
            localized = final_metrics.get("n_frames", 0)
            partial = bool(aborted or error) or (localized != frames_read)
            missing = frames_read - localized
            if not (aborted or error) and missing != 0:
                # A clean run that dropped frames is a real defect: surface it
                # loudly and mark the record partial rather than lying about
                # coverage. (Raising here would bypass the record write; we log
                # + flag so the partial FOV is still recorded, honestly.)
                logger.error(
                    "live-analysis no-subsample invariant VIOLATED: read {} "
                    "frames but localized {} ({} missing) on a clean finish".format(
                        frames_read, localized, missing
                    )
                )
            self.hub.push_kind(
                "metrics", run_id, metrics=final_metrics, backend=backend_stats
            )

            # Archive the raw movie (fallback path); skipped when on the pool.
            try:
                archive_result = archive_movie(
                    cfg.movie_source_path,
                    cfg.archive_dir,
                    write_target=cfg.write_target,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("archive step raised: {!r}".format(exc))

            # Post the per-FOV registry record (best-effort; never blocks).
            # Coverage provenance rides on the record so partial coverage is
            # explicit, never silent: frames_read vs frames_localized, a
            # partial/aborted flag, and the missing count.
            coverage = {
                "frames_read": frames_read,
                "frames_localized": localized,
                "frames_missing": missing,
                "partial": partial,
                "aborted": bool(aborted),
                "errored": bool(error),
            }
            payload = build_fov_payload(
                run_id=run_id,
                metrics=final_metrics,
                fov=cfg.fov_fields,
                acquisition=cfg.acquisition_fields,
                analysis=cfg.analysis_fields,
                coverage=coverage,
            )
            registry_ids = self._post_record(run_id, payload)

            state = (
                "fov_aborted"
                if aborted
                else ("fov_error" if error else "fov_done")
            )
            self.hub.push_kind("state", run_id, state=state)

        return FovResult(
            run_id=run_id,
            metrics=final_metrics,
            frames_read=frames_read,
            backend_stats=backend_stats,
            interlock=interlock_result,
            archive=archive_result,
            registry_ids=registry_ids,
            aborted=aborted,
            error=error,
            coverage=coverage,
            abort_generation=abort_generation,
        )

    # -- internals ----------------------------------------------------------
    def _push_metrics(self, run_id, metrics: RunningMetrics, backend) -> None:
        self.hub.push_kind(
            "metrics",
            run_id,
            metrics=metrics.snapshot(),
            backend=backend.stats(),
        )

    def _post_record(self, run_id, payload: dict) -> dict | None:
        return surface_record(
            self.hub, run_id, self._registry, payload, post_fov_record
        )


# Re-exported so callers don't reach into archive.py for the constants.
__all__ = [
    "LiveAnalysisService",
    "FovConfig",
    "FovResult",
    "WRITE_TARGET_LOCAL",
    "WRITE_TARGET_POOL",
    "DEFAULT_LOCALIZE_PARAMS",
    "surface_record",
]
