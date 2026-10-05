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

from PycroFlow.live_analysis.run_id import new_run_id


def _ensure_id(fields: dict) -> str:
    """Pre-mint the row id so the FK chain never needs the server response.

    The WP-3 ``BufferedRegistryClient``'s writes are fire-and-forget — they
    return an acknowledgement (``{"buffered": True}``), not the created row —
    so chaining on the response id would KeyError with the production client.
    The registry honors client-supplied ids (crud mints only when absent),
    and a pre-minted id also makes at-least-once replay dedup exactly.
    """
    if not fields.get("id"):
        fields["id"] = new_run_id()
    return fields["id"]


def _row_id(response: Any, fallback: str) -> str:
    """The server-confirmed id when the client returns rows, else ours."""
    if isinstance(response, dict) and response.get("id"):
        return response["id"]
    return fallback


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

    Writes acquisition_run -> fov -> analysis_run -> metrics. Row ids are
    pre-minted client-side (see :func:`_ensure_id`) so the chain works with
    the fire-and-forget ``BufferedRegistryClient`` as well as the synchronous
    ``RegistryClient`` / in-memory ``MockRegistryClient`` (whose returned row
    ids are preferred when present). Returns the created ids.
    """
    acq_fields = dict(payload["acquisition_run"])
    acq_id = _ensure_id(acq_fields)
    acq_id = _row_id(client.log_acquisition(**acq_fields), acq_id)

    fov_fields = dict(payload["fov"])
    fov_fields["acquisition_run_id"] = acq_id
    fov_id = _ensure_id(fov_fields)
    fov_id = _row_id(client.log_fov(**fov_fields), fov_id)

    an_fields = dict(payload["analysis_run"])
    an_fields["acquisition_run_id"] = acq_id
    an_fields["fov_id"] = fov_id
    an_id = _ensure_id(an_fields)
    an_id = _row_id(client.log_analysis(**an_fields), an_id)

    metric_fields = dict(payload["metrics"])
    metric_fields["analysis_run_id"] = an_id
    metrics_id = _ensure_id(metric_fields)
    metrics_id = _row_id(client.log_metrics(**metric_fields), metrics_id)

    return {
        "acquisition_run_id": acq_id,
        "fov_id": fov_id,
        "analysis_run_id": an_id,
        "metrics_id": metrics_id,
    }


def build_experiment_payload(
    *,
    experiment_id: str,
    run_id: str,
    design: dict | None = None,
    setup_name: str | None = None,
) -> dict:
    """Assemble the experiment-level registry record (WP-LIVE-INT).

    One row per orchestrated run, written at run start; the per-FOV
    ``acquisition_run`` rows link to it via ``acquisition_run.experiment_id``.
    It carries what the Experiment Design already knows (the experiment type
    and run naming) under ``extra`` — the A2 descriptor axes (taxon, target,
    modality) are populated by the cohort/experiment layer, not here.

    Parameters
    ----------
    experiment_id : str
        The minted ULID that becomes ``experiment.id``.
    run_id : str
        The experiment's acquisition run_id (recorded for the join).
    design : dict or None
        The validated Experiment Design dict (aliased keys).
    setup_name : str or None
        The microscope setup name, when the frontend knows it.
    """
    design = design if isinstance(design, dict) else {}
    exp = design.get("experiment") or {}
    extra = {
        "run_id": run_id,
        "experiment_type": exp.get("type"),
        "base_name": design.get("base_name"),
        "setup": setup_name,
    }
    return {
        "id": experiment_id,
        "extra": {k: v for k, v in extra.items() if v is not None},
    }


def post_experiment_record(client: Any, payload: dict) -> dict:
    """Post a :func:`build_experiment_payload` record through a client.

    Parameters
    ----------
    client : Any
        A registry client exposing the ``log_*`` surface (real, buffered, or
        the in-memory mock).
    payload : dict
        The experiment row from :func:`build_experiment_payload`.

    Returns
    -------
    dict
        The client's response — the created row for synchronous clients, a
        ``{"buffered": True}`` acknowledgement for the fire-and-forget one
        (callers fall back to the payload's pre-minted ``id``).
    """
    return client.log_experiment(**payload)
