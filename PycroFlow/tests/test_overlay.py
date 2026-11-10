"""Tests for the monitor-movie subtitle overlay (step name + elapsed)."""

from __future__ import annotations

import unittest

import numpy as np

from PycroFlow.monitoring.overlay import (
    burn_subtitle_gray,
    burn_subtitle_rgb,
    format_elapsed,
    subtitle_text,
)


class TestSubtitleText(unittest.TestCase):
    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(0), "0:00")
        self.assertEqual(format_elapsed(5), "0:05")
        self.assertEqual(format_elapsed(75), "1:15")
        self.assertEqual(format_elapsed(3661), "1:01:01")
        self.assertEqual(format_elapsed(-3), "0:00")

    def test_subtitle_text(self):
        self.assertEqual(subtitle_text("inject I1", 75), "inject I1  ·  1:15")
        # No label -> just the clock.
        self.assertEqual(subtitle_text("", 5), "0:05")
        self.assertEqual(subtitle_text(None, 5), "0:05")


class TestBurnRgb(unittest.TestCase):
    def test_overlays_bottom_only(self):
        frame = np.zeros((90, 240, 3), dtype=np.uint8)
        out = burn_subtitle_rgb(frame, "inject I1  ·  0:05")
        self.assertEqual(out.shape, frame.shape)
        self.assertEqual(out.dtype, np.uint8)
        # Something was drawn (white text), and the top is untouched.
        self.assertGreater(int(out.sum()), 0)
        self.assertEqual(int(out[:40].sum()), 0)

    def test_empty_text_is_noop(self):
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        self.assertIs(burn_subtitle_rgb(frame, ""), frame)

    def test_non_rgb_is_noop(self):
        frame = np.zeros((20, 40), dtype=np.uint8)
        self.assertIs(burn_subtitle_rgb(frame, "x"), frame)


class TestBurnGray(unittest.TestCase):
    def test_overlays_and_preserves_dtype(self):
        frame = np.full((64, 64), 100, dtype=np.uint16)
        out = burn_subtitle_gray(frame, "acquire  ·  0:02")
        self.assertEqual(out.shape, frame.shape)
        self.assertEqual(out.dtype, np.uint16)
        # The input is not mutated; the output differs in the bottom strip.
        self.assertTrue(
            np.array_equal(frame, np.full((64, 64), 100, "uint16"))
        )
        self.assertFalse(np.array_equal(out, frame))
        # Top rows untouched; the bright text reaches the dtype max.
        self.assertTrue(np.array_equal(out[:30], frame[:30]))
        self.assertEqual(int(out.max()), np.iinfo(np.uint16).max)

    def test_uint8_frame(self):
        frame = np.full((48, 96), 10, dtype=np.uint8)
        out = burn_subtitle_gray(frame, "incubate  ·  0:10")
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(int(out.max()), 255)

    def test_too_small_is_noop(self):
        frame = np.zeros((4, 4), dtype=np.uint16)
        self.assertIs(burn_subtitle_gray(frame, "x"), frame)

    def test_empty_text_is_noop(self):
        frame = np.zeros((40, 40), dtype=np.uint16)
        self.assertIs(burn_subtitle_gray(frame, ""), frame)


if __name__ == "__main__":
    unittest.main()
