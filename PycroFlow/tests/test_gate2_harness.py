"""Hermetic tests for the Gate-2 harness's own logic (emulator mode only).

These cover the harness scaffolding, not the WP-4 pipeline (that is tested in
``test_live_analysis.py``): the check-runner isolates a crashing check; the
emulator run produces ``gate2_pass=True`` with every criterion present; the
individual reconciliation / interlock / archive checks return the right verdict;
and the verdict serialises to the documented JSON shape.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest

from PycroFlow.perf import gate2_harness as g2


def _emulator_args(**overrides):
    """A fully-populated emulator-mode args namespace (small + fast)."""
    base = dict(
        mode=g2.MODE_EMULATOR,
        n_frames=300,
        batch_size=50,
        n_workers=2,
        output_dir=None,
        data_dir=None,
        registry_url=None,
        registry_token=None,
        mm_config=None,
        mm_port=4827,
        monet_host=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestCheckRunner(unittest.TestCase):
    def test_crashing_check_is_recorded_as_failed_not_raised(self):
        runner = g2.CheckRunner()

        def _boom():
            raise RuntimeError("kaboom")

        chk = runner.run("boomer", "x", _boom)
        self.assertFalse(chk.passed)
        self.assertIn("kaboom", chk.detail)
        self.assertFalse(runner.gate2_pass)

    def test_all_pass_gate2_true_empty_runner_false(self):
        empty = g2.CheckRunner()
        self.assertFalse(empty.gate2_pass)  # no checks != pass
        runner = g2.CheckRunner()
        runner.add(g2.Check("a", "c", True))
        runner.add(g2.Check("b", "c", True))
        self.assertTrue(runner.gate2_pass)
        runner.add(g2.Check("c", "c", False))
        self.assertFalse(runner.gate2_pass)


class TestEmulatorEndToEnd(unittest.TestCase):
    def test_emulator_run_is_gate2_pass_with_all_criteria(self):
        args = _emulator_args()
        verdict = g2.run_gate2(args)
        self.assertTrue(verdict["gate2_pass"], verdict["failures"])
        names = {c["name"] for c in verdict["checks"]}
        self.assertEqual(
            names,
            {
                "keep_up",
                "no_silent_subsample",
                "live_metrics_real",
                "registry_record",
                "laser_interlock",
                "pycromanager_1_0",
                "archive",
            },
        )
        self.assertEqual(verdict["failures"], [])
        self.assertEqual(verdict["mode"], g2.MODE_EMULATOR)

    def test_coverage_check_reconciles_frames(self):
        args = _emulator_args(n_frames=257)
        verdict = g2.run_gate2(args)
        cov = next(
            c for c in verdict["checks"] if c["name"] == "no_silent_subsample"
        )
        self.assertTrue(cov["passed"])
        self.assertEqual(cov["values"]["frames_read"], 257)
        self.assertEqual(cov["values"]["frames_localized"], 257)
        self.assertFalse(cov["values"]["partial"])
        self.assertTrue(cov["values"]["run_id"])

    def test_live_metrics_nena_is_real_non_none(self):
        args = _emulator_args()
        verdict = g2.run_gate2(args)
        m = next(
            c for c in verdict["checks"] if c["name"] == "live_metrics_real"
        )
        self.assertTrue(m["passed"])
        self.assertIsNotNone(m["values"]["nena_px"])
        self.assertGreater(m["values"]["nena_px"], 0)

    def test_pycromanager_check_is_skipped_pass_in_emulator(self):
        args = _emulator_args()
        vb = g2.build_version_block(g2.MODE_EMULATOR)
        chk = g2.check_pycromanager_1_0(args, vb)
        self.assertTrue(chk.passed)
        self.assertTrue(chk.values["skipped"])


class TestIndividualChecks(unittest.TestCase):
    def test_laser_interlock_all_three_paths_safe(self):
        chk = g2.check_laser_interlock(_emulator_args())
        self.assertTrue(chk.passed)
        self.assertEqual(
            set(chk.values["paths"]),
            {"normal_end", "early_abort", "injected_exception"},
        )
        self.assertTrue(all(chk.values["paths"].values()))

    def test_archive_verified_and_local_deleted(self):
        chk = g2.check_archive(_emulator_args())
        self.assertTrue(chk.passed)
        self.assertTrue(chk.values["verified"])
        self.assertTrue(chk.values["local_deleted"])


class TestVerdictIO(unittest.TestCase):
    def test_write_verdict_shape_and_roundtrip(self):
        verdict = g2.run_gate2(_emulator_args())
        with tempfile.TemporaryDirectory() as tmp:
            path = g2.write_verdict(verdict, tmp)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        for key in (
            "schema_version",
            "gate",
            "gate2_pass",
            "mode",
            "version_block",
            "checks",
            "failures",
            "config",
        ):
            self.assertIn(key, loaded)
        self.assertEqual(loaded["gate"], "gate2")
        for c in loaded["checks"]:
            self.assertEqual(
                set(c), {"name", "criterion", "passed", "detail", "values"}
            )

    def test_main_emulator_returns_zero_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = g2.main(
                ["--mode", "emulator", "--n-frames", "200", "--out", tmp]
            )
            self.assertEqual(rc, 0)
            written = [f for f in os.listdir(tmp) if f.endswith(".json")]
            self.assertEqual(len(written), 1)


if __name__ == "__main__":
    unittest.main()
