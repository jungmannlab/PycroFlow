"""Hermetic tests for the WP-1 performance harness (emulator mode).

These are everything Claude can run without the instrument: the emulator frame
source exercises the *same* measurement / output / analysis code the real
instrument run uses, so the code path is proven before real data arrives.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from PycroFlow.perf import schema
from PycroFlow.perf.backends import FrameSourceBackend
from PycroFlow.perf.cli import main as cli_main
from PycroFlow.perf.config import EmulatorParams, PerfConfig
from PycroFlow.perf.harness import run_and_write, run_config, run_sweep


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


class TestReaderModeConfig(unittest.TestCase):
    def test_valid_modes(self):
        self.assertEqual(
            PerfConfig(mode="emulator", reader_mode="thread").reader_mode,
            "thread",
        )
        self.assertEqual(
            PerfConfig(mode="emulator", reader_mode="process").reader_mode,
            "process",
        )

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            PerfConfig(mode="emulator", reader_mode="bogus")


class _FakeExternalBackend(FrameSourceBackend):
    """Backend that claims to manage its own out-of-process reader."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._n = cfg.n_frames
        self.events = []

    def start(self):
        self.events.append(("start",))

    def occupancy(self):
        return 0

    def capacity(self):
        return 100

    def produced(self):
        return self._n

    def written(self):
        return self._n

    def dropped(self):
        return 0

    def available_for_read(self):
        return 0

    def read_batch(self, n):
        self.events.append(("read_batch", n))
        return 0

    def production_done(self):
        return True

    def all_written(self):
        return True

    def describe(self):
        return {"backend": "fake"}

    def close(self):
        self.events.append(("close",))

    def external_reader(self):
        return True

    def start_external_reader(self, batch_size):
        self.events.append(("start_reader", batch_size))

    def stop_external_reader(self):
        self.events.append(("stop_reader",))


class TestExternalReaderBranch(unittest.TestCase):
    def test_external_reader_started_and_stopped_not_read_batch(self):
        cfg = PerfConfig(
            mode="emulator",
            n_frames=10,
            monitor_interval_s=0.005,
        )
        fake = _FakeExternalBackend(cfg)
        with mock.patch(
            "PycroFlow.perf.harness.make_backend", return_value=fake
        ):
            run_config(cfg, reader_on=True, batch_size=16)
        self.assertIn(("start_reader", 16), fake.events)
        self.assertIn(("stop_reader",), fake.events)
        # The harness must NOT also drive the in-process read loop.
        self.assertFalse(any(e[0] == "read_batch" for e in fake.events))


class TestIncrementalWriting(unittest.TestCase):
    def test_meta_marks_complete_and_lists_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PerfConfig(
                mode="emulator",
                frame_rate_hz=400.0,
                n_frames=150,
                buffer_mb=160.0,
                batch_sizes=[8, 64],
                monitor_interval_s=0.005,
                output_dir=tmp,
            )
            run_dir = run_and_write(cfg)
            schema.validate_run_dir(run_dir)
            meta = schema.read_run_meta(run_dir)
            self.assertEqual(meta["status"], "complete")
            self.assertEqual(len(meta["completed_configs"]), 3)
            self.assertEqual(meta["errors"], [])

    def test_earlier_results_survive_a_later_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PerfConfig(
                mode="emulator",
                n_frames=100,
                batch_sizes=[8, 64],
                monitor_interval_s=0.005,
                output_dir=tmp,
            )

            calls = {"n": 0}
            real_first = {
                "mode": cfg.mode,
                "reader": False,
                "batch_size": 0,
                "n_frames": cfg.n_frames,
                "frame_rate_hz": cfg.frame_rate_hz,
                "buffer_mb": cfg.buffer_mb,
                "buffer_frames": cfg.buffer_capacity_frames(),
                "frame_bytes": cfg.frame_bytes(),
                "frames_produced": cfg.n_frames,
                "frames_written": cfg.n_frames,
                "dropped_count": 0,
                "dropped_fraction": 0.0,
                "occupancy_peak": 0,
                "occupancy_mean": 0.0,
                "throughput_fps": 100.0,
                "duration_s": 1.0,
            }
            ts_first = [
                {
                    "mode": cfg.mode,
                    "reader": False,
                    "batch_size": 0,
                    "sample_index": 0,
                    "t_rel_s": 0.0,
                    "frame_index": 0,
                    "occupancy": 0,
                }
            ]

            def fake_run_config(cfg_, reader_on, batch):
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_first, ts_first, {"backend": "emulator"}
                raise RuntimeError("simulated acquisition failure")

            with mock.patch(
                "PycroFlow.perf.harness.run_config",
                side_effect=fake_run_config,
            ):
                run_dir = run_and_write(cfg)

            # The run dir is still schema-valid and holds the first (baseline)
            # result plus a recorded error — nothing was lost.
            schema.validate_run_dir(run_dir)
            loaded = schema.load_run_dir(run_dir)
            self.assertEqual(len(loaded["metrics"]), 1)
            meta = loaded["meta"]
            self.assertEqual(meta["status"], "error")
            self.assertEqual(len(meta["errors"]), 1)
            self.assertEqual(len(meta["completed_configs"]), 1)


class TestReaderProcessFastPath(unittest.TestCase):
    def test_stop_file_present_exits_without_sdk(self):
        from PycroFlow.perf.reader_process import main

        with tempfile.TemporaryDirectory() as tmp:
            stop = os.path.join(tmp, "stop")
            count = os.path.join(tmp, "count")
            open(stop, "w").close()
            rc = main(
                [
                    "--acq-dir",
                    tmp,
                    "--batch",
                    "10",
                    "--stop-file",
                    stop,
                    "--count-file",
                    count,
                ]
            )
            self.assertEqual(rc, 0)
            with open(count, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), "0")


class TestSchemaValidation(unittest.TestCase):
    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                schema.validate_run_dir(tmp)


if __name__ == "__main__":
    unittest.main()
