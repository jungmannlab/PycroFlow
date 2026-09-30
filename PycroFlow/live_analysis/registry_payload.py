"""Build + post the per-FOV provenance record to picasso-registry.

The registry is the append-only spine everything joins on by ``run_id``. On a
FOV completing, the service snapshots the live metrics and writes one record:

  acquisition_run (id = the minted ULID run_id)
    -> fov (frame_count, rate, position)
       -> analysis_run (kind="live_localize", compute_location)
          -> metrics (n_locs, spots_per_frame, background, nena_nm, scope="live")

Only these live-analysis fields are written here (the descriptor axes / cohort
keys are populated elsewhere — S0B-1b / the experiment layer). Every row carries
``run_id`` so the record joins cleanly. :func:`build_fov_payload` produces the
plain-dict payload (used by the golden test); :func:`post_fov_record` posts it
through a registry client (real or the in-memory mock).
"""

from __future__ import annotations

from typing import Any


def build_fov_payload(
    *,
    run_id: str,
    metrics: dict,
    fov: dict | None = None,
    acquisition: dict | None = None,
    analysis: dict | None = None,
    coverage: dict | None = None,
) -> dict:
    """Assemble the per-FOV registry record as nested plain dicts.

    Parameters
    ----------
    run_id : str
        The minted ULID; becomes the ``acquisition_run.id`` and tags every row.
    metrics : dict
        A :class:`~PycroFlow.live_analysis.metrics.RunningMetrics` snapshot.
    fov, acquisition, analysis : dict or None
        Optional extra fields merged into the respective rows (e.g. ``pos_x``,
        ``frame_rate_hz``, ``compute_location``).
    coverage : dict or None
        Coverage provenance from the run (``frames_read`` / ``frames_localized``
        / ``frames_missing`` / ``partial`` / ``aborted`` / ``errored``). When it
        marks the run partial, the acquisition status becomes
        ``live_localized_partial`` and the analysis status ``aborted`` /
        ``errored`` (else ``done``); the full coverage dict is stored on the
        ``fov`` and ``analysis_run`` ``extra`` columns so partial coverage is
        explicit and queryable, never silent.
    """
    coverage = coverage or {}
    partial = bool(coverage.get("partial", False))

    acq = {
        "id": run_id,
        "status": "live_localized_partial" if partial else "live_localized",
        "raw_retained": True,
    }
    acq.update(acquisition or {})

    fov_row = {
        "acquisition_run_id": run_id,
        "frame_count": metrics.get("n_frames"),
    }
    if coverage:
        fov_row["extra"] = {"coverage": coverage}
    fov_row.update(fov or {})

    if coverage.get("aborted"):
        analysis_status = "aborted"
    elif coverage.get("errored"):
        analysis_status = "errored"
    else:
        analysis_status = "done"
    analysis_row = {
        "acquisition_run_id": run_id,
        "kind": "live_localize",
        "status": analysis_status,
        "compute_location": "local-subprocess",
    }
    if coverage:
        analysis_row["extra"] = {"coverage": coverage}
    analysis_row.update(analysis or {})

    metrics_row = {
        "scope": "live",
        "n_locs": metrics.get("n_locs"),
        "spots_per_frame": metrics.get("spots_per_frame"),
        "background": metrics.get("background"),
        "nena_nm": metrics.get("nena_nm"),
    }

    return {
        "run_id": run_id,
        "acquisition_run": acq,
        "fov": fov_row,
        "analysis_run": analysis_row,
        "metrics": metrics_row,
    }


def post_fov_record(client: Any, payload: dict) -> dict:
    """Post a :func:`build_fov_payload` record through a registry client.

    Writes acquisition_run -> fov -> analysis_run -> metrics, threading the
    server-assigned ids down the FK chain, and returns the created ids. Works
    with the real ``RegistryClient`` and the in-memory ``MockRegistryClient``
    (both share the ``log_*`` surface).
    """
    acq = client.log_acquisition(**payload["acquisition_run"])

    fov_fields = dict(payload["fov"])
    fov_fields["acquisition_run_id"] = acq["id"]
    fov = client.log_fov(**fov_fields)

    an_fields = dict(payload["analysis_run"])
    an_fields["acquisition_run_id"] = acq["id"]
    an_fields["fov_id"] = fov["id"]
    analysis = client.log_analysis(**an_fields)

    metric_fields = dict(payload["metrics"])
    metric_fields["analysis_run_id"] = analysis["id"]
    metrics = client.log_metrics(**metric_fields)

    return {
        "acquisition_run_id": acq["id"],
        "fov_id": fov["id"],
        "analysis_run_id": analysis["id"],
        "metrics_id": metrics.get("id"),
    }
