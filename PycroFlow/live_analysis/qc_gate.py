"""Rule-based QC gate — mark a QC-passed dataset ready for cluster hand-off.

WP-9 Phase B. When a live/preprocess run finishes, a small **deterministic,
explainable** rule evaluator scores the run's final metrics (a
:class:`~PycroFlow.live_analysis.metrics.RunningMetrics` snapshot) against
**configurable thresholds** (:class:`QcThresholds`, carried on the run config —
never hard-coded). On a pass the dataset is marked **ready** in the registry
hand-off queue (``registry.mark_ready``), so the Phase-C cluster-submit worker
can ``claim`` it; on a fail the dataset is NOT readied and the reasons are
recorded.

The gate is **best-effort / non-blocking** — the load-bearing invariant of the
whole live path (same as WP-1 / the registry writes): a registry outage, a
duplicate ready, or any client error is swallowed and logged, NEVER raised, so
it can never stall or break acquisition. Registry unset ⇒ the gate is a no-op
and the run is byte-identical to today.

The verdict is a plain dict (``passed`` / ``reasons`` / ``metrics``) so it can
ride on a registry record / log without pulling in any domain type.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass
class QcThresholds:
    """Thresholds for the rule-based QC hand-off gate (all optional).

    Each threshold is a separate, independently-overridable rule; a ``None``
    threshold **disables** that rule (it cannot fail the run). Defaults are
    deliberately permissive/sane so an unconfigured run still readies on any
    non-empty localization result — the per-cohort tightening is learned
    elsewhere (WP-7) and supplied via the run config, not hand-set here.

    Parameters
    ----------
    min_n_locs : int or None
        Minimum total localizations. Default 1 (a run that localized
        *something* passes; an empty run fails). ``None`` disables the rule.
    max_nena_nm : float or None
        Maximum acceptable NeNA (experimental precision, nm) — smaller is
        better, so the run fails when NeNA exceeds this. Default ``None``
        (disabled; NeNA bands are cohort-learned, not a fixed global ceiling).
        When enabled and NeNA could not be computed (``nena_nm`` missing), the
        rule fails (we cannot certify precision we never measured).
    min_spots_per_frame : float or None
        Minimum mean localizations per frame (a crude density/blinking floor).
        Default ``None`` (disabled). ``None`` for the metric value also fails
        an enabled rule.
    """

    min_n_locs: int | None = 1
    max_nena_nm: float | None = None
    min_spots_per_frame: float | None = None


def evaluate_qc(metrics: dict | None, thresholds: QcThresholds) -> dict:
    """Score a metrics snapshot against the QC thresholds (pure, deterministic).

    Parameters
    ----------
    metrics : dict or None
        A :class:`~PycroFlow.live_analysis.metrics.RunningMetrics` snapshot
        (``n_locs`` / ``nena_nm`` / ``spots_per_frame`` / …). ``None`` or an
        empty dict is treated as "no metrics" and fails every enabled rule.
    thresholds : QcThresholds
        The rules to apply; a ``None`` threshold disables its rule.

    Returns
    -------
    dict
        ``{"passed": bool, "reasons": list[str], "metrics": dict}`` — ``reasons``
        lists every failed rule (empty on a pass); ``metrics`` echoes the
        values the rules read, so the verdict is self-explaining in a log/record.
    """
    m = metrics or {}
    reasons: list[str] = []
    read: dict = {}

    if thresholds.min_n_locs is not None:
        n_locs = m.get("n_locs")
        read["n_locs"] = n_locs
        if n_locs is None or n_locs < thresholds.min_n_locs:
            reasons.append(
                "n_locs {} < min_n_locs {}".format(
                    n_locs, thresholds.min_n_locs
                )
            )

    if thresholds.max_nena_nm is not None:
        nena = m.get("nena_nm")
        read["nena_nm"] = nena
        if nena is None:
            reasons.append(
                "nena_nm missing (max_nena_nm {} enabled)".format(
                    thresholds.max_nena_nm
                )
            )
        elif nena > thresholds.max_nena_nm:
            reasons.append(
                "nena_nm {} > max_nena_nm {}".format(
                    nena, thresholds.max_nena_nm
                )
            )

    if thresholds.min_spots_per_frame is not None:
        spf = m.get("spots_per_frame")
        read["spots_per_frame"] = spf
        if spf is None or spf < thresholds.min_spots_per_frame:
            reasons.append(
                "spots_per_frame {} < min_spots_per_frame {}".format(
                    spf, thresholds.min_spots_per_frame
                )
            )

    return {"passed": not reasons, "reasons": reasons, "metrics": read}


def qc_gate_mark_ready(
    registry,
    *,
    acquisition_run_id: str,
    metrics: dict | None,
    thresholds: QcThresholds | None,
    analysis_run_id: str | None = None,
    coverage: dict | None = None,
) -> dict:
    """Evaluate the QC rule and, on a pass, mark the dataset ready. Best-effort.

    The single wiring point for the hand-off gate: it evaluates
    :func:`evaluate_qc` on the final metrics and, **iff it passes**, calls
    ``registry.mark_ready(acquisition_run_id=...)`` so the cluster worker can
    claim the dataset. A fail does NOT ready the dataset.

    Never raises and never blocks: ``registry is None`` (registry disabled) is a
    no-op; a partial/aborted/errored run short-circuits to a fail (don't hand a
    known-incomplete dataset to the cluster); a duplicate ready (409) or any
    client error is swallowed + logged. A registry outage must never break
    acquisition.

    Parameters
    ----------
    registry : object or None
        The registry client (real / buffered / in-memory mock). ``None`` ⇒ the
        gate is a no-op (registry disabled).
    acquisition_run_id : str
        The run's ``acquisition_run`` id — must already exist in the registry
        (``mark_ready``'s FK). The caller ensures the record is posted first.
    metrics : dict or None
        The final metrics snapshot scored by the rule.
    thresholds : QcThresholds or None
        The rule config; ``None`` uses permissive defaults.
    analysis_run_id : str or None
        The live analysis_run id, recorded on the hand-off row when known.
    coverage : dict or None
        Coverage provenance; a ``partial`` / ``aborted`` / ``errored`` run
        fails the gate regardless of metric values.

    Returns
    -------
    dict
        The ``evaluate_qc`` verdict, augmented with ``"readied": bool`` (whether
        ``mark_ready`` was actually called and did not error).
    """
    thresholds = thresholds if thresholds is not None else QcThresholds()
    verdict = evaluate_qc(metrics, thresholds)

    cov = coverage or {}
    if cov.get("partial") or cov.get("aborted") or cov.get("errored"):
        verdict["passed"] = False
        verdict["reasons"] = list(verdict["reasons"]) + ["coverage incomplete"]

    verdict["readied"] = False

    if not verdict["passed"]:
        logger.info(
            "QC gate: {} not ready ({})".format(
                acquisition_run_id, "; ".join(verdict["reasons"]) or "failed"
            )
        )
        return verdict

    if registry is None:
        # Registry disabled: the gate is a no-op (byte-identical to today).
        return verdict

    try:
        registry.mark_ready(
            acquisition_run_id,
            analysis_run_id=analysis_run_id,
            detail={"gate": "rule", "metrics": verdict["metrics"]},
        )
        verdict["readied"] = True
        logger.info("QC gate: {} marked ready".format(acquisition_run_id))
    except Exception as exc:  # noqa: BLE001 - a registry outage isn't fatal
        # Swallow EVERYTHING (incl. a 409 duplicate ready) — the gate never
        # blocks or breaks acquisition.
        logger.warning(
            "QC gate: mark_ready({}) failed, best-effort ignored: {!r}".format(
                acquisition_run_id, exc
            )
        )

    return verdict
