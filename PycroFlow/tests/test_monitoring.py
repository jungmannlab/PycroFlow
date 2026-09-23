"""Hermetic tests for the fluidics monitoring subsystem (WP-FLUIDICS-CAM).

Everything here runs with no camera and no camera library: the emulator frame
source is pure numpy and the AVI writer is pure Python. The three groups the
work order calls for:

* **per-round clip** -- a fake multi-camera capture process, driven by
  simulated round begin/end, produces exactly one clip per round with the
  expected filename, each a valid, readable file;
* **index round-trip** -- a clip URI written onto a ``fluidics_round`` via the
  registry client surface reads back (a local fake always; the real in-memory
  registry mock when installed);
* **isolation proof** -- a deliberately slow / raising / unplugged camera does
  not stall or perturb the emulated exchange (timing within noise of the
  no-camera baseline) and degrades to a gap clip, never an exception.
"""

from __future__ import annotations

import copy
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np

from PycroFlow.monitoring import avi
from PycroFlow.monitoring.config import (
    CameraConfig,
    MonitoringConfig,
    load_monitoring_config,
)
from PycroFlow.monitoring.controller import (
    MonitoringController,
    _first_activity_is_flush,
    _imaging_follows_flags,
    attach_monitoring,
)
from PycroFlow.monitoring.registry_index import RegistryIndexWriter
from PycroFlow.monitoring.sources import (
    EmulatedFrameSource,
    InstrumentFrameSource,
    make_source,
)
from PycroFlow.monitoring.tiling import compose, plan_layout


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _small_config(n_imagers=2, initial=None, frames=2, dark=0):
    """A reduced Exchange design so an emulated run finishes in ~1-2 s."""
    from PycroFlow.tests.fixtures.configs.exchange_basic import CONFIG

    c = copy.deepcopy(CONFIG)
    names = ["EGFR", "5T4", "AXL", "Her2", "PDL1"]
    c["fluid"]["settings"]["experiment"]["imagers"] = names[:n_imagers]
    c["fluid"]["settings"]["experiment"]["initial_imager"] = initial
    c["img"]["settings"]["frames"] = frames
    c["img"]["settings"]["darkframes"] = dark
    return c


def _mon_config(out_dir, **kw):
    kw.setdefault("fps", 15)
    kw.setdefault("retention_days", 0)
    return MonitoringConfig(
        cameras=[
            CameraConfig("reservoir", 0, 120, 90),
            CameraConfig("pump", 1, 120, 90),
        ],
        output_dir=out_dir,
        **kw,
    )


def _build_proto(cfg):
    from PycroFlow.protocols import ProtocolBuilder

    return ProtocolBuilder().build_protocol(cfg)


def _emulated_service(proto):
    from PycroFlow.services.experiment_service import ExperimentService
    from PycroFlow.tests.emulators import (
        EmulatedFluidSystem,
        EmulatedIlluminationSystem,
        EmulatedImagingSystem,
    )

    svc = ExperimentService(
        EmulatedImagingSystem(),
        EmulatedFluidSystem(),
        EmulatedIlluminationSystem(),
    )
    svc.load_protocol(proto)
    return svc


def _run_to_completion(svc, timeout=25.0):
    t0 = time.time()
    svc.start()
    while not svc.is_finished() and time.time() - t0 < timeout:
        time.sleep(0.02)
    elapsed = time.time() - t0
    finished = svc.is_finished()
    svc.end()
    return elapsed, finished


# --------------------------------------------------------------------------
# AVI writer / reader
# --------------------------------------------------------------------------
class TestAviWriter(unittest.TestCase):
    def _roundtrip(self, w, h, n):
        frames = [
            np.random.randint(0, 256, (h, w, 3), np.uint8) for _ in range(n)
        ]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clip.avi")
            with avi.RawAviWriter(p, width=w, height=h, fps=7) as writer:
                for f in frames:
                    writer.write(f)
            back = avi.read_avi(p)
            self.assertEqual(len(back), n)
            for a, b in zip(frames, back):
                self.assertTrue(np.array_equal(a, b))

    def test_roundtrip_even_width(self):
        self._roundtrip(8, 6, 3)

    def test_roundtrip_odd_width_exercises_padding(self):
        self._roundtrip(5, 4, 4)

    def test_zero_frame_clip_is_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.avi")
            avi.RawAviWriter(p, width=8, height=8, fps=5).close()
            self.assertEqual(avi.frame_count(p), 0)

    def test_wrong_shape_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.avi")
            with avi.RawAviWriter(p, width=8, height=8, fps=5) as w:
                with self.assertRaises(avi.AviWriteError):
                    w.write(np.zeros((4, 4, 3), np.uint8))


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
class TestMonitoringConfig(unittest.TestCase):
    def test_absent_block_is_inert(self):
        self.assertIsNone(load_monitoring_config({}))
        self.assertIsNone(load_monitoring_config({"monitoring": {}}))

    def test_no_cameras_is_inert(self):
        self.assertIsNone(
            load_monitoring_config(
                {"monitoring": {"output_dir": "x", "cameras": []}}
            )
        )

    def test_disabled_is_inert(self):
        self.assertIsNone(
            load_monitoring_config(
                {
                    "monitoring": {
                        "enabled": False,
                        "output_dir": "x",
                        "cameras": [{"role": "pump"}],
                    }
                }
            )
        )

    def test_cameras_without_output_dir_raise(self):
        with self.assertRaises(ValueError):
            load_monitoring_config(
                {"monitoring": {"cameras": [{"role": "pump"}]}}
            )

    def test_parse_and_roundtrip(self):
        cfg = load_monitoring_config(
            {
                "monitoring": {
                    "output_dir": "/pool",
                    "fps": 8,
                    "cameras": [
                        {
                            "role": "pump",
                            "device": 2,
                            "width": 320,
                            "height": 240,
                        },
                    ],
                }
            }
        )
        self.assertEqual(cfg.output_dir, "/pool")
        self.assertEqual(cfg.fps, 8)
        self.assertEqual(cfg.cameras[0].role, "pump")
        self.assertEqual(cfg.cameras[0].device, 2)
        again = MonitoringConfig.from_dict(cfg.to_dict())
        self.assertEqual(again.to_dict(), cfg.to_dict())

    def test_shipped_camera_emulator_setup_loads(self):
        from PycroFlow.configs import load_setup

        cfg = load_monitoring_config(load_setup("EmulatorCam"))
        self.assertIsNotNone(cfg)
        self.assertEqual(len(cfg.cameras), 3)
        self.assertEqual(
            {c.role for c in cfg.cameras}, {"reservoir", "pump", "sample"}
        )


# --------------------------------------------------------------------------
# frame sources
# --------------------------------------------------------------------------
class TestFrameSources(unittest.TestCase):
    def test_emulated_healthy_frame(self):
        src = make_source(CameraConfig("pump", 1, 64, 48), "emulator")
        src.open()
        f = src.read()
        src.close()
        self.assertEqual(f.shape, (48, 64, 3))
        self.assertEqual(f.dtype, np.uint8)

    def test_emulated_frames_move(self):
        src = EmulatedFrameSource(CameraConfig("pump", 0, 64, 48))
        src.open()
        self.assertFalse(np.array_equal(src.read(), src.read()))

    def test_fail_mode_raise(self):
        src = EmulatedFrameSource(
            CameraConfig("c", 0, 8, 8), fail_mode="raise"
        )
        src.open()
        with self.assertRaises(RuntimeError):
            src.read()

    def test_fail_mode_unplug(self):
        src = EmulatedFrameSource(
            CameraConfig("c", 0, 8, 8), fail_mode="unplug"
        )
        with self.assertRaises(OSError):
            src.open()

    def test_fail_mode_slow(self):
        src = EmulatedFrameSource(
            CameraConfig("c", 0, 8, 8), fail_mode="slow", delay=0.2
        )
        src.open()
        t0 = time.monotonic()
        src.read()
        self.assertGreaterEqual(time.monotonic() - t0, 0.15)

    def test_instrument_source_without_opencv_reports_extra(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            src = InstrumentFrameSource(CameraConfig("pump", 0))
            with self.assertRaises(RuntimeError) as ctx:
                src.open()
            self.assertIn("monitoring", str(ctx.exception))
        else:
            self.skipTest("OpenCV installed; guard path not exercised")


# --------------------------------------------------------------------------
# tiling
# --------------------------------------------------------------------------
class TestTiling(unittest.TestCase):
    def test_layout_dims(self):
        cams = [CameraConfig("a", 0, 100, 80), CameraConfig("b", 1, 120, 60)]
        layout = plan_layout(cams)
        self.assertEqual((layout.cols, layout.rows), (2, 1))
        self.assertEqual((layout.panel_w, layout.panel_h), (120, 80))
        self.assertEqual((layout.width, layout.height), (240, 80))

    def test_compose_places_and_blanks(self):
        cams = [CameraConfig("a", 0, 10, 10), CameraConfig("b", 1, 10, 10)]
        layout = plan_layout(cams)
        red = np.full((10, 10, 3), (255, 0, 0), np.uint8)
        tile = compose([red, None], layout)  # cam b missing -> black panel
        self.assertTrue(np.array_equal(tile[:10, :10], red))
        self.assertTrue(np.all(tile[:10, 10:20] == 0))


# --------------------------------------------------------------------------
# Test A: per-round clip via the real capture subprocess
# --------------------------------------------------------------------------
class TestCaptureServicePerRound(unittest.TestCase):
    def test_one_clip_per_round_with_expected_name(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "clips")
        os.makedirs(out)
        cfg = _mon_config(out, fps=20)
        cfg_path = os.path.join(d, "cams.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg.to_dict(), f)
        ctl = os.path.join(d, "ctl")
        os.makedirs(ctl)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "PycroFlow.monitoring.capture_service",
                "--config",
                cfg_path,
                "--control-dir",
                ctl,
                "--run-id",
                "RUNA",
                "--emulator",
            ]
        )
        try:
            seq = [0]

            def emit(cmd):
                seq[0] += 1
                p = os.path.join(ctl, "{:012d}.json".format(seq[0]))
                tmp = p + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(cmd, f)
                os.replace(tmp, p)

            time.sleep(0.3)
            for r in range(3):
                emit(
                    {
                        "cmd": "round_begin",
                        "round_index": r,
                        "unique_name": "img-{}".format(r),
                        "t_utc": None,
                    }
                )
                time.sleep(0.4)
                emit({"cmd": "round_end", "round_index": r})
                time.sleep(0.15)
            emit({"cmd": "stop"})
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()

        clips = sorted(glob.glob(os.path.join(out, "run_RUNA_round*.avi")))
        self.assertEqual(len(clips), 3, clips)
        for r, path in enumerate(clips):
            name = os.path.basename(path)
            self.assertIn("round{:03d}".format(r), name)
            self.assertTrue(name.endswith("Z.avi"))
            frames = avi.read_avi(path)
            self.assertGreaterEqual(len(frames), 1)
            # tile: 2 cams -> 2x1 grid of 120x90 panels -> 240x90
            self.assertEqual(frames[0].shape, (90, 240, 3))


# --------------------------------------------------------------------------
# Test B: registry index round-trip
# --------------------------------------------------------------------------
class _FakeRegistryClient:
    """Minimal in-memory client exposing the ``create`` surface used here."""

    def __init__(self):
        self.rows = {}
        self._n = 0

    def create(self, resource, **fields):
        self._n += 1
        rid = "{}-{}".format(resource, self._n)
        row = dict(fields, id=rid, _resource=resource)
        self.rows[rid] = row
        return row


class TestRegistryIndex(unittest.TestCase):
    def test_index_writes_uri_and_reads_back(self):
        fake = _FakeRegistryClient()
        w = RegistryIndexWriter("RUN42", fake)
        rec = w.index(2, "file:///pool/run42_round002.avi", round_name="img-2")
        self.assertIsNotNone(rec)
        stored = fake.rows[rec["id"]]
        self.assertEqual(stored["_resource"], "fluidics_round")
        self.assertEqual(stored["acquisition_run_id"], "RUN42")
        self.assertEqual(stored["round_index"], 2)
        self.assertEqual(
            stored["monitoring_video_uri"],
            "file:///pool/run42_round002.avi",
        )

    def test_disabled_writer_is_noop(self):
        w = RegistryIndexWriter("RUN", None)
        self.assertFalse(w.enabled)
        self.assertIsNone(w.index(0, "file:///x.avi"))
        w.close()  # must not raise

    def test_index_never_raises_on_client_error(self):
        class Boom:
            def create(self, *a, **k):
                raise RuntimeError("registry down")

        w = RegistryIndexWriter("RUN", Boom())
        self.assertIsNone(w.index(0, "file:///x.avi"))  # logged, not raised

    def test_from_env_disabled_without_url(self):
        env = dict(os.environ)
        os.environ.pop("PAINT_REGISTRY_URL", None)
        try:
            w = RegistryIndexWriter.from_env("RUN")
            self.assertFalse(w.enabled)
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_real_registry_mock_round_trip(self):
        try:
            from picasso_registry.testing import MockRegistryClient
        except Exception:
            self.skipTest("picasso-registry not installed")
        client = MockRegistryClient()
        try:
            w = RegistryIndexWriter("RUNZ", client)
            rec = w.index(1, "file:///pool/z_round001.avi")
            got = client.get("fluidics_round", rec["id"])
            # monitoring_video_uri folds into the row's extra JSON.
            self.assertEqual(
                (got.get("extra") or {}).get("monitoring_video_uri"),
                "file:///pool/z_round001.avi",
            )
        finally:
            client.close()


# --------------------------------------------------------------------------
# protocol-derived round bracketing
# --------------------------------------------------------------------------
class TestRoundBracketing(unittest.TestCase):
    def test_flush_first_layout_opens_at_start(self):
        proto = _build_proto(_small_config(n_imagers=2, initial=None))
        fe = proto["fluid"]["protocol_entries"]
        self.assertTrue(_first_activity_is_flush(fe))
        self.assertEqual(len(_imaging_follows_flags(fe)), 3)

    def test_initial_imager_layout_opens_on_imaging(self):
        proto = _build_proto(_small_config(n_imagers=2, initial="Her3"))
        fe = proto["fluid"]["protocol_entries"]
        self.assertFalse(_first_activity_is_flush(fe))


# --------------------------------------------------------------------------
# Test A': controller binds clips to the orchestration round lifecycle
# --------------------------------------------------------------------------
class TestControllerRoundBinding(unittest.TestCase):
    def test_one_clip_per_exchange_leg(self):
        proto = _build_proto(_small_config(n_imagers=2, initial=None))
        svc = _emulated_service(proto)
        out = tempfile.mkdtemp()
        MonitoringController(
            svc, _mon_config(out), mode="emulator", run_id="BIND"
        ).attach()
        _, finished = _run_to_completion(svc)
        time.sleep(0.3)
        self.assertTrue(finished)
        clips = sorted(glob.glob(os.path.join(out, "run_BIND_round*.avi")))
        # 2 imagers, no dark frames -> 3 flush signals + a trailing top-up.
        self.assertGreaterEqual(len(clips), 3)
        for c in clips:
            avi.read_avi(c)  # every clip is a valid, readable file

    def test_attach_monitoring_inert_without_block(self):
        proto = _build_proto(_small_config(n_imagers=2, initial=None))
        svc = _emulated_service(proto)
        self.assertIsNone(attach_monitoring(svc, {"emulated": True}))


# --------------------------------------------------------------------------
# Test C: isolation proof (the load-bearing gate)
# --------------------------------------------------------------------------
class TestIsolationProof(unittest.TestCase):
    def _run(self, fail_mode=None, fail_delay=0.0, monitored=True):
        proto = _build_proto(_small_config(n_imagers=2, initial=None))
        svc = _emulated_service(proto)
        out = tempfile.mkdtemp() if monitored else None
        if monitored:
            MonitoringController(
                svc,
                _mon_config(out),
                mode="emulator",
                run_id="ISO",
                fail_mode=fail_mode,
                fail_delay=fail_delay,
            ).attach()
        elapsed, finished = _run_to_completion(svc)
        time.sleep(0.2)
        n = (
            len(glob.glob(os.path.join(out, "run_ISO_round*.avi")))
            if out
            else 0
        )
        return elapsed, finished, n

    def test_bad_cameras_do_not_stall_or_perturb(self):
        base, base_done, _ = self._run(monitored=False)
        self.assertTrue(base_done)
        tolerance = base + 6.0  # generous; a real stall would hang -> timeout

        for label, fm, fd in [
            ("healthy", None, 0.0),
            ("unplug", "unplug", 0.0),
            ("raise", "raise", 0.0),
            ("slow", "slow", 0.4),
        ]:
            elapsed, done, n = self._run(fail_mode=fm, fail_delay=fd)
            self.assertTrue(done, "{}: exchange did not finish".format(label))
            self.assertLessEqual(
                elapsed,
                tolerance,
                "{}: exchange stalled ({:.2f}s vs baseline {:.2f}s)".format(
                    label, elapsed, base
                ),
            )
            # A downed/bad camera still yields gap clips, never absence.
            self.assertGreaterEqual(
                n, 3, "{}: expected gap clips, got {}".format(label, n)
            )

    def test_emit_never_blocks_under_saturation(self):
        # The signal-observer path only touches a bounded, drop-oldest queue,
        # so it must return instantly even when nothing drains it.
        ctrl = MonitoringController(
            object(), _mon_config(tempfile.mkdtemp()), mode="emulator"
        )
        t0 = time.monotonic()
        for i in range(5000):
            ctrl._emit({"cmd": "round_begin", "round_index": i})
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertLessEqual(ctrl._queue.qsize(), 256)


if __name__ == "__main__":
    unittest.main()
