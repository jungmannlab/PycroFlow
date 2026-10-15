"""Tests for the post-run replay recorder + controller.

The recorder serializes the live streams (seam updates + progress / fluid-state
polls) to a JSONL timeline; the controller loads it and, on seek, reconstructs
each stream's state as of a timeline position and pushes it to sinks. These
cover the serialization round-trip (image bytes + boxes preserved) and the
last-event-at-or-before-t seek semantics, headlessly (no Qt).
"""

import os
import tempfile
import unittest

from PycroFlow.live_analysis.client_seam import LiveUpdate
from PycroFlow.live_analysis.replay_recorder import (
    ReplayRecorder,
    encode_payload,
    decode_payload,
)
from PycroFlow.live_analysis.replay_player import ReplayController


class _Clock:
    """Deterministic monotonic clock advancing a fixed step per call."""

    def __init__(self, times):
        self._times = list(times)
        self._i = 0

    def __call__(self):
        v = self._times[min(self._i, len(self._times) - 1)]
        self._i += 1
        return v


class TestPayloadCodec(unittest.TestCase):

    def test_bytes_round_trip(self):
        payload = {"data": b"\x00\x01\x02", "shape": (2, 2), "dtype": "uint8"}
        encoded = encode_payload(payload)
        # Encoded form is JSON-safe (bytes -> __b64__, tuple -> list).
        self.assertIsInstance(encoded["data"], dict)
        self.assertEqual(encoded["shape"], [2, 2])
        restored = decode_payload(encoded)
        self.assertEqual(restored["data"], b"\x00\x01\x02")


class TestReplayRecorder(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "x_replay.jsonl")

    def test_records_updates_progress_and_fluid(self):
        clock = _Clock([0.0, 0.5, 1.0, 1.5, 2.0])
        rec = ReplayRecorder(self.path, clock=clock)
        rec.start("RUN")  # t0 = 0.0
        rec.on_update(
            LiveUpdate(
                "thumbnail",
                "RUN",
                {
                    "data": b"\x01\x02\x03\x04",
                    "shape": [2, 2],
                    "dtype": "uint8",
                    "boxes": [1.0, 2.0, 3.0, 4.0],
                    "box_size": 3,
                },
            )
        )  # t = 0.5 -> 500 ms
        rec.record_progress({"fluid": (1, 3), "img": (0, 2)})  # 1000 ms
        rec.record_fluid_state({"pump_a": {"valve": "in"}})  # 1500 ms
        rec.stop()

        ctrl = ReplayController.from_file(self.path)
        # 3 data events + the meta header line.
        self.assertEqual(ctrl.event_count, 4)
        self.assertEqual(ctrl.duration_ms, 1500)
        # The meta header carries the wall-clock anchor for webcam alignment.
        self.assertIsNotNone(ctrl.t0_utc)
        # Thumbnail bytes + boxes survive the JSONL round-trip.
        thumb = ctrl.last_event_at(2000, "thumbnail")
        restored = decode_payload(thumb["payload"])
        self.assertEqual(restored["data"], b"\x01\x02\x03\x04")
        self.assertEqual(restored["boxes"], [1.0, 2.0, 3.0, 4.0])

    def test_never_raises_without_start(self):
        rec = ReplayRecorder(self.path)
        # Writing before start is a no-op, not an error.
        rec.on_update(LiveUpdate("metrics", "R", {"metrics": {}}))
        rec.record_progress({"img": (0, 1)})
        rec.stop()
        self.assertFalse(os.path.exists(self.path))


class TestReplayController(unittest.TestCase):

    def _controller(self, **sinks):
        events = [
            {
                "t_ms": 0,
                "kind": "state",
                "payload": {"state": "experiment_started"},
            },
            {
                "t_ms": 500,
                "kind": "thumbnail",
                "payload": {
                    "data": encode_payload({"d": b"\x09"})["d"],
                    "shape": [1, 1],
                    "dtype": "uint8",
                },
            },
            {
                "t_ms": 1000,
                "kind": "progress",
                "payload": {"fluid": [2, 5], "img": [1, 3]},
            },
            {
                "t_ms": 1500,
                "kind": "fluid_state",
                "payload": {"pump_a": {"valve": "out"}},
            },
            {
                "t_ms": 2000,
                "kind": "progress",
                "payload": {"fluid": [4, 5], "img": [2, 3]},
            },
        ]
        return ReplayController(events, **sinks)

    def test_last_event_at_semantics(self):
        ctrl = self._controller()
        # Before any progress event -> None.
        self.assertIsNone(ctrl.last_event_at(400, "progress"))
        # Between the two progress events -> the first.
        self.assertEqual(
            ctrl.last_event_at(1200, "progress")["payload"]["fluid"], [2, 5]
        )
        # After the second -> the second.
        self.assertEqual(
            ctrl.last_event_at(9999, "progress")["payload"]["fluid"], [4, 5]
        )

    def test_seek_dispatches_last_per_kind(self):
        emitted, progs, fluids = [], [], []
        ctrl = self._controller(
            emit_update=emitted.append,
            set_progress=progs.append,
            set_fluid_state=fluids.append,
        )
        ctrl.seek(1200)
        # live kinds: state (0) + thumbnail (500) emitted.
        kinds = [u.kind for u in emitted]
        self.assertIn("state", kinds)
        self.assertIn("thumbnail", kinds)
        # progress tuple restored (list -> tuple) from the 1000 ms snapshot.
        self.assertEqual(progs[-1]["fluid"], (2, 5))
        # No fluid_state yet at 1200 (first at 1500).
        self.assertEqual(fluids, [])

    def test_seek_to_end_uses_latest(self):
        progs = []
        ctrl = self._controller(set_progress=progs.append)
        ctrl.seek(5000)
        self.assertEqual(progs[-1]["img"], (2, 3))

    def test_webcam_sink_called_with_time(self):
        seen = []
        ctrl = self._controller()
        ctrl.set_webcam_sink(seen.append)
        ctrl.seek(1234)
        self.assertEqual(seen, [1234])


class TestWebcamReplaySource(unittest.TestCase):

    _UTC_FMT = "%Y%m%dT%H%M%SZ"

    def _write_clip(self, clip_dir, round_index, utc_dt, frame_values, fps=5):
        import numpy as np
        from PycroFlow.monitoring.avi import RawAviWriter

        stamp = utc_dt.strftime(self._UTC_FMT)
        name = "run_RID_round{:03d}_fluid-step{}_{}.avi".format(
            round_index, round_index, stamp
        )
        path = os.path.join(clip_dir, name)
        with RawAviWriter(path, width=4, height=4, fps=fps) as w:
            for v in frame_values:
                w.write(np.full((4, 4, 3), v, dtype=np.uint8))
        return path

    def test_discovers_aligns_and_seeks_clips(self):
        import datetime as _dt
        from PycroFlow.monitoring.webcam_replay import WebcamReplaySource

        clip_dir = tempfile.mkdtemp()
        t0 = _dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=_dt.timezone.utc)
        t0_utc = t0.timestamp()
        # Clip 0 starts at t0 (0 ms); clip 1 two seconds later (2000 ms).
        self._write_clip(clip_dir, 0, t0, [10, 11, 12, 13, 14])
        self._write_clip(
            clip_dir, 1, t0 + _dt.timedelta(seconds=2), [100, 101, 102]
        )

        src = WebcamReplaySource.from_dir(clip_dir, t0_utc, fps=5)
        self.assertEqual(src.clip_count, 2)
        # t=0 -> clip 0 frame 0 (value 10).
        self.assertEqual(int(src.frame_at(0)[0, 0, 0]), 10)
        # t=400 ms -> clip 0 frame 2 (0.4 s * 5 fps = 2) (value 12).
        self.assertEqual(int(src.frame_at(400)[0, 0, 0]), 12)
        # t=2200 ms -> clip 1 frame 1 (0.2 s * 5 = 1) (value 101).
        self.assertEqual(int(src.frame_at(2200)[0, 0, 0]), 101)
        # Before the first clip -> no frame.
        self.assertIsNone(src.frame_at(-50))
        # Past the end clamps to the last clip's last frame (value 102).
        self.assertEqual(int(src.frame_at(10_000)[0, 0, 0]), 102)
        src.close()

    def test_empty_dir_is_harmless(self):
        from PycroFlow.monitoring.webcam_replay import WebcamReplaySource

        src = WebcamReplaySource.from_dir(tempfile.mkdtemp(), 1.0, fps=5)
        self.assertEqual(src.clip_count, 0)
        self.assertIsNone(src.frame_at(1000))

    def test_missing_anchor_yields_no_clips(self):
        from PycroFlow.monitoring.webcam_replay import WebcamReplaySource

        src = WebcamReplaySource.from_dir(tempfile.mkdtemp(), None, fps=5)
        self.assertEqual(src.clip_count, 0)


if __name__ == "__main__":
    unittest.main()
