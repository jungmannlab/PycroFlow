"""Hermetic tests for the Gate-2 harness's own logic (emulator mode only).

These cover the harness scaffolding, not the WP-4 pipeline (that is tested in
``test_live_analysis.py``): the check-runner isolates a crashing check; the
emulator run produces ``gate2_pass=True`` with every criterion present; the
individual reconciliation / interlock / archive checks return the right verdict;
and the verdict serialises to the documented JSON shape.
"""

from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile
import time
import unittest

from PycroFlow.perf import gate2_harness as g2


def _emulator_args(**overrides):
    """A fully-populated emulator-mode args namespace (small + fast).

    Built from the real parser defaults so it never drifts from the CLI, then
    overridden for a quick hermetic run.
    """
    args = g2.build_parser().parse_args(["--mode", g2.MODE_EMULATOR])
    args.n_frames = 300
    args.batch_size = 50
    args.n_workers = 2
    args.timeout_s = 60.0
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


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


class TestInMemoryRegistryStub(unittest.TestCase):
    def test_implements_post_fov_record_surface_and_readback(self):
        from PycroFlow.live_analysis.registry_payload import (
            build_fov_payload,
            post_fov_record,
        )

        stub = g2.InMemoryRegistryStub()
        run_id = "01STUBRUNID0000000000000000"
        payload = build_fov_payload(
            run_id=run_id,
            metrics={
                "n_locs": 10,
                "n_frames": 100,
                "spots_per_frame": 0.1,
                "background": 100.0,
                "nena_nm": 3.0,
            },
        )
        ids = post_fov_record(stub, payload)
        # The acquisition_run id IS the run_id (the check reads it back by run_id).
        self.assertEqual(ids["acquisition_run_id"], run_id)
        acq = stub.get("acquisition_run", run_id)
        self.assertEqual(acq["status"], "live_localized")
        self.assertTrue(acq["raw_retained"])
        fov = stub.get("fov", ids["fov_id"])
        self.assertEqual(fov["frame_count"], 100)
        an = stub.get("analysis_run", ids["analysis_run_id"])
        self.assertEqual(an["kind"], "live_localize")
        # Exactly one record per resource.
        self.assertEqual(len(stub.records("acquisition_run")), 1)

    def test_get_missing_raises(self):
        stub = g2.InMemoryRegistryStub()
        with self.assertRaises(KeyError):
            stub.get("fov", "nope")


class TestMockPathIsFastapiFree(unittest.TestCase):
    """The emulator / no-URL registry path must not import fastapi or the
    picasso_registry server app — the acq PC has only ``[client]`` installed.
    """

    def test_make_registry_does_not_import_fastapi_or_registry_app(self):
        # Make fastapi + the registry server modules un-importable, so any
        # attempt to reach them on the mock path raises rather than silently
        # succeeding because they happen to be installed in this container.
        blocked = (
            "fastapi",
            "picasso_registry.testing",
            "picasso_registry.app",
        )
        real_import = builtins.__import__

        def guarded_import(name, *a, **kw):
            if name in blocked or any(
                name.startswith(b + ".") for b in blocked
            ):
                raise ImportError("blocked in test: {}".format(name))
            return real_import(name, *a, **kw)

        # Drop any cached copies so a re-import would actually run.
        saved = {
            k: sys.modules.pop(k)
            for k in list(sys.modules)
            if k in blocked or any(k.startswith(b + ".") for b in blocked)
        }
        builtins.__import__ = guarded_import
        try:
            args = _emulator_args()
            client, close = g2._make_registry(args)
            self.assertIsInstance(client, g2.InMemoryRegistryStub)
            close()
            # A full emulator run must still be green with fastapi blocked.
            verdict = g2.run_gate2(args)
            self.assertTrue(verdict["gate2_pass"], verdict["failures"])
            rec = next(
                c for c in verdict["checks"] if c["name"] == "registry_record"
            )
            self.assertTrue(rec["passed"])
        finally:
            builtins.__import__ = real_import
            sys.modules.update(saved)


class TestWatchdog(unittest.TestCase):
    """The watchdog must catch a stalled FOV: interlock fired, non-zero exit,
    verdict written, and NO hang — the exact failure that hung the acq PC.
    """

    def _patch_hanging_source(self):
        """Patch make_frame_source with one whose batches() blocks forever."""
        import PycroFlow.live_analysis.frame_source as fs_mod
        from PycroFlow.live_analysis.frame_source import MockFrameSource

        class _Hanging(MockFrameSource):
            def batches(self, batch_size):
                # Never yields: models a tiff-tail source polling an empty
                # data_dir because no MDA ever started (the acq-PC hang).
                while not self._stop.is_set():
                    time.sleep(0.02)
                return
                yield  # pragma: no cover - unreachable, makes this a generator

        orig = fs_mod.make_frame_source
        fs_mod.make_frame_source = lambda kind, **kw: _Hanging(
            n_frames=1000, height=16, width=16
        )
        return fs_mod, orig

    def test_stalled_fov_fires_interlock_and_returns_stalled(self):
        fs_mod, orig = self._patch_hanging_source()
        illu = g2.FakeIllumination()
        registry = g2.InMemoryRegistryStub()
        args = _emulator_args(timeout_s=0.5)
        try:
            start = time.monotonic()
            fov = g2.run_clean_fov(args, registry, illu)
            elapsed = time.monotonic() - start
        finally:
            fs_mod.make_frame_source = orig
            registry.close()
        # It returned (did not hang) shortly after the timeout.
        self.assertLess(elapsed, 20.0)
        self.assertTrue(fov.stalled)
        self.assertIsNone(fov.result)
        # SAFETY: the interlock fired — lasers off + shutter closed.
        self.assertTrue(illu.all_off)
        self.assertFalse(illu.shutter_open)

    def test_stalled_run_gate2_is_fail_with_stall_verdict(self):
        fs_mod, orig = self._patch_hanging_source()
        args = _emulator_args(timeout_s=0.5)
        try:
            verdict = g2.run_gate2(args)
        finally:
            fs_mod.make_frame_source = orig
        self.assertFalse(verdict["gate2_pass"])
        self.assertTrue(verdict["stalled"])
        self.assertEqual(verdict["stalled_phase"], "clean_fov")
        # The four FOV-derived checks fail with a stall detail; archive still ran.
        by_name = {c["name"]: c for c in verdict["checks"]}
        self.assertFalse(by_name["no_silent_subsample"]["passed"])
        self.assertTrue(by_name["no_silent_subsample"]["values"]["stalled"])
        self.assertTrue(by_name["archive"]["passed"])

    def test_main_stalled_returns_nonzero_and_writes_verdict(self):
        fs_mod, orig = self._patch_hanging_source()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                rc = g2.main(
                    [
                        "--mode",
                        "emulator",
                        "--n-frames",
                        "1000",
                        "--timeout",
                        "0.5",
                        "--out",
                        tmp,
                    ]
                )
                written = [f for f in os.listdir(tmp) if f.endswith(".json")]
                self.assertEqual(len(written), 1)
                with open(
                    os.path.join(tmp, written[0]), encoding="utf-8"
                ) as fh:
                    v = json.load(fh)
        finally:
            fs_mod.make_frame_source = orig
        self.assertEqual(rc, 1)
        self.assertTrue(v["stalled"])


if __name__ == "__main__":
    unittest.main()
