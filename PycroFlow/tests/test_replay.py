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
        self.assertEqual(ctrl.event_count, 3)
        self.assertEqual(ctrl.duration_ms, 1500)
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
            {"t_ms": 0, "kind": "state",
             "payload": {"state": "experiment_started"}},
            {"t_ms": 500, "kind": "thumbnail",
             "payload": {"data": encode_payload({"d": b"\x09"})["d"],
                         "shape": [1, 1], "dtype": "uint8"}},
            {"t_ms": 1000, "kind": "progress",
             "payload": {"fluid": [2, 5], "img": [1, 3]}},
            {"t_ms": 1500, "kind": "fluid_state",
             "payload": {"pump_a": {"valve": "out"}}},
            {"t_ms": 2000, "kind": "progress",
             "payload": {"fluid": [4, 5], "img": [2, 3]}},
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


if __name__ == "__main__":
    unittest.main()
