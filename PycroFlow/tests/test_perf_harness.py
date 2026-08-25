"""Hermetic tests for the WP-1 performance harness (emulator mode).

These are everything Claude can run without the instrument: the emulator frame
source exercises the *same* measurement / output / analysis code the real
instrument run uses, so the code path is proven before real data arrives.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from PycroFlow.perf import schema
from PycroFlow.perf.cli import main as cli_main
from PycroFlow.perf.config import EmulatorParams, PerfConfig
from PycroFlow.perf.harness import run_and_write, run_sweep


class TestEmulatorRunDir(unittest.TestCase):
    def test_run_produces_valid_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PerfConfig(
                mode="emulator",
                frame_rate_hz=400.0,
                n_frames=200,
                buffer_mb=160.0,
                batch_sizes=[8, 64],
                monitor_interval_s=0.005,
                output_dir=tmp,
            )
            run_dir = run_and_write(cfg)

            # Conforms to the documented schema.
            schema.validate_run_dir(run_dir)

            loaded = schema.load_run_dir(run_dir)
            # One baseline + one row per batch size.
            self.assertEqual(len(loaded["metrics"]), 3)
            readers = sorted(r["reader"] for r in loaded["metrics"])
            self.assertEqual(readers, [False, True, True])

            meta = loaded["meta"]
            for key in schema.RUN_META_REQUIRED_KEYS:
                self.assertIn(key, meta)
            self.assertEqual(meta["schema_version"], schema.SCHEMA_VERSION)
            self.assertEqual(meta["mode"], "emulator")
            self.assertEqual(meta["backend"]["backend"], "emulator")

            # Every metrics row wrote all frames and the timeseries is
            # non-empty and tagged.
            for row in loaded["metrics"]:
                self.assertEqual(row["frames_written"], cfg.n_frames)
            self.assertTrue(loaded["timeseries"])
            modes = {r["mode"] for r in loaded["timeseries"]}
            self.assertEqual(modes, {"emulator"})

    def test_baseline_stays_clean(self):
        """With realistic defaults, no drops and near-empty buffer."""
        cfg = PerfConfig(
            mode="emulator",
            frame_rate_hz=400.0,
            n_frames=200,
            buffer_mb=160.0,
            batch_sizes=[8, 64],
            monitor_interval_s=0.005,
        )
        metrics, _, _ = run_sweep(cfg)
        for row in metrics:
            self.assertEqual(row["dropped_count"], 0)
            self.assertLessEqual(row["occupancy_peak"], 5)


class TestBackpressureDetection(unittest.TestCase):
    def test_slow_reader_raises_occupancy_vs_baseline(self):
        """A synthetic slow reader must measurably raise occupancy / drops.

        This is the core sanity check: the harness can *detect* contention.
        """
        cfg = PerfConfig(
            mode="emulator",
            frame_rate_hz=200.0,
            n_frames=300,
            buffer_mb=160.0,
            batch_sizes=[64],
            monitor_interval_s=0.005,
            include_baseline=True,
            emulator=EmulatorParams(
                write_speed_factor=1.2,
                contention=8.0,
                read_cost_per_frame_s=0.003,
            ),
        )
        metrics, _, _ = run_sweep(cfg)
        baseline = next(r for r in metrics if not r["reader"])
        with_reader = next(r for r in metrics if r["reader"])

        # Occupancy is clearly higher with the slow reader...
        self.assertGreater(
            with_reader["occupancy_peak"], baseline["occupancy_peak"]
        )
        self.assertGreaterEqual(with_reader["occupancy_peak"], 10)
        # ...and back-pressure shows up as drops and/or a throughput hit.
        self.assertTrue(
            with_reader["dropped_count"] > baseline["dropped_count"]
            or with_reader["throughput_fps"]
            < 0.95 * baseline["throughput_fps"]
        )


class TestCli(unittest.TestCase):
    def test_emulator_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = cli_main(
                [
                    "--emulator",
                    "--frames",
                    "200",
                    "--frame-rate",
                    "400",
                    "--batch-sizes",
                    "8,64",
                    "--monitor-interval",
                    "0.005",
                    "--out",
                    tmp,
                ]
            )
            self.assertEqual(rc, 0)
            dirs = [
                d for d in os.listdir(tmp) if d.startswith("wp1_emulator_")
            ]
            self.assertEqual(len(dirs), 1)
            schema.validate_run_dir(os.path.join(tmp, dirs[0]))


class TestBufferSizing(unittest.TestCase):
    def test_buffer_mb_to_frames(self):
        # 1024x1024x2 = 2 MiB/frame -> 160 MB buffer = 80 frames.
        cfg = PerfConfig(mode="emulator", buffer_mb=160.0)
        self.assertEqual(cfg.frame_bytes(), 1024 * 1024 * 2)
        self.assertEqual(cfg.buffer_capacity_frames(), 80)

    def test_roi_sets_frame_size(self):
        # Centre 512x512 quadrant -> 0.5 MiB/frame -> 160 MB = 320 frames.
        cfg = PerfConfig(
            mode="emulator", buffer_mb=160.0, roi=[256, 256, 512, 512]
        )
        self.assertEqual(cfg.frame_bytes(), 512 * 512 * 2)
        self.assertEqual(cfg.buffer_capacity_frames(), 320)


class TestInstrumentGuards(unittest.TestCase):
    def test_instrument_requires_data_dir(self):
        # pycromanager is mocked in the test env; start() must refuse to run
        # without a data_dir rather than risk writing the raw movie into the
        # repo.
        from PycroFlow.perf.backends import InstrumentBackend

        cfg = PerfConfig(mode="instrument", data_dir=None)
        backend = InstrumentBackend(cfg)
        with self.assertRaises(ValueError):
            backend.start()


class TestSchemaValidation(unittest.TestCase):
    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                schema.validate_run_dir(tmp)


if __name__ == "__main__":
    unittest.main()
