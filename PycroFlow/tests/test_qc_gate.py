"""Hermetic tests for the WP-9 rule-based hand-off QC gate.

Two tiers, both instrument-free:

* the pure rule evaluator (``evaluate_qc``) + the best-effort ready helper
  (``qc_gate_mark_ready``) — threshold boundaries, config overrides,
  empty/missing metrics, registry-unset no-op, registry-outage swallow. These
  need NO registry install (they use a trivial fake / None);
* end-to-end against the in-memory MOCK registry: a passing run readies the
  dataset (``handoff`` row in state ``ready``), a failing run does not, and a
  registry-unset run is byte-identical. These SKIP when the registry (its
  FastAPI test app) isn't importable, matching the repo's existing convention.
"""

from __future__ import annotations

import unittest

from PycroFlow.live_analysis.qc_gate import (
    QcThresholds,
    evaluate_qc,
    qc_gate_mark_ready,
)
from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService

_LOC_PARAMS = {"Box Size": 7, "Min. Net Gradient": 1000}


class _RaisingRegistry:
    """A registry whose ``mark_ready`` always raises (simulated outage)."""

    def __init__(self):
        self.calls = 0

    def mark_ready(self, *a, **k):
        self.calls += 1
        raise RuntimeError("registry down")


class _RecordingRegistry:
    """A registry that records ``mark_ready`` calls (no real backend)."""

    def __init__(self):
        self.ready = []

    def mark_ready(self, acquisition_run_id, **k):
        self.ready.append((acquisition_run_id, k))
        return {"acquisition_run_id": acquisition_run_id, "state": "ready"}


class TestEvaluateQc(unittest.TestCase):
    """The pure, deterministic rule evaluator."""

    def test_default_passes_on_any_locs(self):
        v = evaluate_qc({"n_locs": 1}, QcThresholds())
        self.assertTrue(v["passed"])
        self.assertEqual(v["reasons"], [])

    def test_empty_metrics_fail_min_locs(self):
        for m in (None, {}, {"n_locs": 0}):
            v = evaluate_qc(m, QcThresholds())
            self.assertFalse(v["passed"])
            self.assertTrue(any("n_locs" in r for r in v["reasons"]))

    def test_min_n_locs_boundary(self):
        th = QcThresholds(min_n_locs=100)
        self.assertFalse(evaluate_qc({"n_locs": 99}, th)["passed"])
        self.assertTrue(evaluate_qc({"n_locs": 100}, th)["passed"])
        self.assertTrue(evaluate_qc({"n_locs": 101}, th)["passed"])

    def test_min_n_locs_none_disables(self):
        # Rule off: even zero locs passes (if nothing else is enabled).
        v = evaluate_qc({"n_locs": 0}, QcThresholds(min_n_locs=None))
        self.assertTrue(v["passed"])

    def test_max_nena_boundary(self):
        th = QcThresholds(min_n_locs=None, max_nena_nm=10.0)
        self.assertTrue(
            evaluate_qc({"nena_nm": 10.0}, th)["passed"]
        )  # <= passes
        self.assertTrue(evaluate_qc({"nena_nm": 9.9}, th)["passed"])
        self.assertFalse(evaluate_qc({"nena_nm": 10.1}, th)["passed"])

    def test_max_nena_missing_fails_when_enabled(self):
        th = QcThresholds(min_n_locs=None, max_nena_nm=10.0)
        v = evaluate_qc({"nena_nm": None}, th)
        self.assertFalse(v["passed"])
        self.assertTrue(any("missing" in r for r in v["reasons"]))

    def test_min_spots_per_frame_boundary(self):
        th = QcThresholds(min_n_locs=None, min_spots_per_frame=2.0)
        self.assertFalse(evaluate_qc({"spots_per_frame": 1.9}, th)["passed"])
        self.assertTrue(evaluate_qc({"spots_per_frame": 2.0}, th)["passed"])

    def test_reasons_accumulate_all_failures(self):
        th = QcThresholds(
            min_n_locs=100, max_nena_nm=5.0, min_spots_per_frame=3.0
        )
        v = evaluate_qc(
            {"n_locs": 1, "nena_nm": 9.0, "spots_per_frame": 0.1}, th
        )
        self.assertFalse(v["passed"])
        self.assertEqual(len(v["reasons"]), 3)

    def test_metrics_echoed_in_verdict(self):
        v = evaluate_qc({"n_locs": 5}, QcThresholds(min_n_locs=1))
        self.assertEqual(v["metrics"]["n_locs"], 5)


class TestQcGateMarkReady(unittest.TestCase):
    """The best-effort ready helper (no real registry required)."""

    def test_passing_run_marks_ready(self):
        reg = _RecordingRegistry()
        v = qc_gate_mark_ready(
            reg,
            acquisition_run_id="run1",
            metrics={"n_locs": 500},
            thresholds=QcThresholds(min_n_locs=100),
            analysis_run_id="an1",
        )
        self.assertTrue(v["passed"])
        self.assertTrue(v["readied"])
        self.assertEqual(reg.ready[0][0], "run1")
        self.assertEqual(reg.ready[0][1]["analysis_run_id"], "an1")

    def test_failing_run_not_readied(self):
        reg = _RecordingRegistry()
        v = qc_gate_mark_ready(
            reg,
            acquisition_run_id="run1",
            metrics={"n_locs": 1},
            thresholds=QcThresholds(min_n_locs=100),
        )
        self.assertFalse(v["passed"])
        self.assertFalse(v["readied"])
        self.assertEqual(reg.ready, [])

    def test_registry_none_is_noop(self):
        v = qc_gate_mark_ready(
            None,
            acquisition_run_id="run1",
            metrics={"n_locs": 500},
            thresholds=QcThresholds(),
        )
        self.assertTrue(v["passed"])
        self.assertFalse(v["readied"])  # no client -> not readied

    def test_registry_outage_swallowed(self):
        reg = _RaisingRegistry()
        v = qc_gate_mark_ready(
            reg,
            acquisition_run_id="run1",
            metrics={"n_locs": 500},
            thresholds=QcThresholds(),
        )
        # The outage is swallowed; the gate returns without raising.
        self.assertTrue(v["passed"])
        self.assertFalse(v["readied"])
        self.assertEqual(reg.calls, 1)

    def test_incomplete_coverage_fails_gate(self):
        reg = _RecordingRegistry()
        v = qc_gate_mark_ready(
            reg,
            acquisition_run_id="run1",
            metrics={"n_locs": 500},
            thresholds=QcThresholds(),
            coverage={"partial": True, "aborted": True},
        )
        self.assertFalse(v["passed"])
        self.assertFalse(v["readied"])
        self.assertIn("coverage incomplete", v["reasons"])
        self.assertEqual(reg.ready, [])


def _mock_registry_or_skip(testcase):
    try:
        from picasso_registry.testing import mock_registry
    except ModuleNotFoundError as exc:  # pragma: no cover - base env only
        testcase.skipTest("mock_registry needs fastapi ({})".format(exc))
    return mock_registry


class FakeIllumination:
    """Minimal illumination stub so the laser interlock is exercised."""

    def __init__(self):
        self.enabled = True

    def disable_all(self):
        self.enabled = False

    def close_shutter(self):
        pass


class TestQcGateEndToEnd(unittest.TestCase):
    """End-to-end through run_fov against the in-memory mock registry."""

    def _run(self, *, registry, qc_thresholds=None, n_frames=200):
        svc = LiveAnalysisService(
            registry_client=registry,
            illumination_system=FakeIllumination(),
        )
        run_id = svc.start_experiment()
        cfg = FovConfig(
            source_kind="mock",
            source_kwargs={
                "n_frames": n_frames,
                "height": 48,
                "width": 48,
                "seed": 5,
            },
            localize_params=_LOC_PARAMS,
            batch_size=50,
            use_processes=False,
            pixelsize_nm=130.0,
            qc_thresholds=qc_thresholds,
        )
        return svc, run_id, svc.run_fov(cfg)

    def test_passing_run_marks_dataset_ready(self):
        mock_registry = _mock_registry_or_skip(self)
        with mock_registry() as reg:
            # Permissive default: a non-empty localization result passes.
            svc, run_id, res = self._run(registry=reg)
            self.assertGreater(res.metrics["n_locs"], 0)
            self.assertIsNotNone(res.qc)
            self.assertTrue(res.qc["passed"])
            self.assertTrue(res.qc["readied"])
            ready = reg.list_handoffs(state="ready")
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0]["acquisition_run_id"], run_id)
            self.assertEqual(ready[0]["state"], "ready")

    def test_failing_run_does_not_mark_ready(self):
        mock_registry = _mock_registry_or_skip(self)
        with mock_registry() as reg:
            # Impossible floor: the run cannot pass -> not readied.
            svc, run_id, res = self._run(
                registry=reg,
                qc_thresholds=QcThresholds(min_n_locs=10**9),
            )
            self.assertIsNotNone(res.qc)
            self.assertFalse(res.qc["passed"])
            self.assertFalse(res.qc["readied"])
            self.assertTrue(res.qc["reasons"])
            self.assertEqual(reg.list_handoffs(state="ready"), [])

    def test_registry_unset_run_identical_no_handoff(self):
        # Registry disabled (None): the gate is a no-op; no qc verdict is
        # produced (no acquisition_run record was posted), run still succeeds.
        svc = LiveAnalysisService(registry_client=None)
        svc.start_experiment()
        cfg = FovConfig(
            source_kind="mock",
            source_kwargs={"n_frames": 150, "height": 32, "width": 32},
            localize_params=_LOC_PARAMS,
            batch_size=50,
            use_processes=False,
        )
        res = svc.run_fov(cfg)
        self.assertIsNone(res.registry_ids)
        self.assertIsNone(res.qc)
        self.assertGreaterEqual(res.metrics["n_locs"], 0)

    def test_duplicate_ready_409_swallowed(self):
        mock_registry = _mock_registry_or_skip(self)
        with mock_registry() as reg:
            svc, run_id, res = self._run(registry=reg)
            self.assertTrue(res.qc["readied"])
            # A second ready for the same dataset is a 409; swallowed.
            v = qc_gate_mark_ready(
                reg,
                acquisition_run_id=run_id,
                metrics={"n_locs": 500},
                thresholds=QcThresholds(),
            )
            self.assertTrue(v["passed"])
            self.assertFalse(v["readied"])  # 409 swallowed, not re-readied
            self.assertEqual(len(reg.list_handoffs(state="ready")), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
