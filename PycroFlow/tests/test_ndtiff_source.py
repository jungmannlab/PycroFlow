"""Hermetic tests for ``NdTiffDatasetFrameSource`` (the acq PC live NDTiff read).

The source tails a pycromanager ``Acquisition.get_dataset()`` (an ndstorage
``Dataset``) along the ``time`` axis. We can't run a real Micro-Manager
acquisition in CI, but the source only needs a duck-typed dataset exposing
``has_image``/``read_image``/``is_finished`` (+ optional ``await_new_image``), so
a fake dataset drives it here — covering both the all-present case (batching /
frame_read accounting) and the LIVE case (frames arriving over time, then
``is_finished`` flips).
"""

from __future__ import annotations

import threading
import time
import unittest

import numpy as np

from PycroFlow.live_analysis.frame_source import NdTiffDatasetFrameSource


class _AllPresentDataset:
    """A finished dataset with all frames already available."""

    def __init__(self, n, shape=(4, 4)):
        self._frames = [np.full(shape, i, dtype=np.uint16) for i in range(n)]

    def has_image(self, time=None, **kw):
        return time is not None and 0 <= time < len(self._frames)

    def read_image(self, time=None, **kw):
        return self._frames[time]

    def is_finished(self):
        return True


class _LiveDataset:
    """Frames arrive over time; ``is_finished`` flips once producing stops."""

    def __init__(self):
        self._frames = {}
        self._finished = False
        self._lock = threading.Lock()

    def add(self, i, arr):
        with self._lock:
            self._frames[i] = arr

    def finish(self):
        with self._lock:
            self._finished = True

    def has_image(self, time=None, **kw):
        with self._lock:
            return time in self._frames

    def read_image(self, time=None, **kw):
        with self._lock:
            return self._frames[time]

    def is_finished(self):
        with self._lock:
            return self._finished

    def await_new_image(self, timeout=None):
        time.sleep(0.005)


class TestNdTiffDatasetFrameSource(unittest.TestCase):
    def test_reads_all_frames_in_contiguous_batches(self):
        ds = _AllPresentDataset(10)
        src = NdTiffDatasetFrameSource(
            ds, pixelsize_nm=130.0, poll_s=0.0, idle_grace_s=0.0
        )
        batches = list(src.batches(batch_size=3))
        # 10 frames @ batch 3 -> 3,3,3,1 with absolute contiguous start_frames.
        self.assertEqual([b.n_frames for b in batches], [3, 3, 3, 1])
        self.assertEqual([b.start_frame for b in batches], [0, 3, 6, 9])
        self.assertTrue(all(b.position is None for b in batches))
        self.assertEqual(src.frames_read(), 10)
        self.assertEqual(src.camera_info().get("Pixelsize"), 130.0)
        # frames come out in order 0..9 (no subsample, no reorder).
        got = np.concatenate([b.frames for b in batches], axis=0)
        self.assertEqual(got.shape[0], 10)
        self.assertEqual(
            [int(got[i, 0, 0]) for i in range(10)], list(range(10))
        )

    def test_no_pixelsize_gives_empty_camera_info(self):
        src = NdTiffDatasetFrameSource(_AllPresentDataset(1))
        self.assertEqual(src.camera_info(), {})

    def test_tails_live_arrivals_until_finished(self):
        ds = _LiveDataset()

        def producer():
            for i in range(6):
                time.sleep(0.02)
                ds.add(i, np.full((2, 2), i, dtype=np.uint16))
            ds.finish()

        src = NdTiffDatasetFrameSource(ds, poll_s=0.005, idle_grace_s=0.05)
        t = threading.Thread(target=producer)
        t.start()
        # batches() blocks/tails until the producer finishes.
        batches = list(src.batches(batch_size=2))
        t.join()
        self.assertEqual(src.frames_read(), 6)
        got = np.concatenate([b.frames for b in batches], axis=0)
        self.assertEqual([int(got[i, 0, 0]) for i in range(6)], list(range(6)))

    def test_close_stops_the_tail(self):
        ds = _LiveDataset()  # never finishes, only 0 frames
        src = NdTiffDatasetFrameSource(ds, poll_s=0.005)
        out = []

        def consume():
            out.extend(src.batches(batch_size=4))

        t = threading.Thread(target=consume)
        t.start()
        time.sleep(0.05)
        src.close()
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive())  # close() broke the poll loop
        self.assertEqual(out, [])  # nothing was ever available

    def test_first_frame_timeout_fails_fast(self):
        # A camera that never produces (and never finishes) must NOT hang until
        # the outer watchdog — the first-frame timeout bails fast.
        ds = _LiveDataset()  # no frames ever added, is_finished() stays False
        src = NdTiffDatasetFrameSource(
            ds, poll_s=0.005, first_frame_timeout_s=0.1
        )
        t0 = time.monotonic()
        out = list(src.batches(batch_size=4))
        elapsed = time.monotonic() - t0
        self.assertEqual(out, [])
        self.assertEqual(src.frames_read(), 0)
        self.assertTrue(src.no_frames_timed_out)
        self.assertLess(elapsed, 5.0)  # bailed ~0.1 s, did not hang


if __name__ == "__main__":
    unittest.main()
