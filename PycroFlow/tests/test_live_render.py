"""Tests for the per-FOV super-resolution render (save-render feature).

The one heavy picasso ``render`` call is isolated in
:mod:`PycroFlow.live_analysis.render` and mocked here (the real call allocates a
large float image and is exercised only in the lab / hosted CI, not pinned in a
memory-bounded unit). These cover the dict/oversampling plumbing, the PNG write
seam, and the service wiring (output-path derivation + best-effort guards).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from PycroFlow.live_analysis import render as render_mod
from PycroFlow.live_analysis.metrics import RunningMetrics
from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService
from PycroFlow.services.imaging_config import build_fov_config


def _locs(n=500, extent=48, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "frame": rng.integers(0, 50, n),
            "x": rng.uniform(0, extent, n).astype("float32"),
            "y": rng.uniform(0, extent, n).astype("float32"),
            "lpx": np.full(n, 0.12, "float32"),
            "lpy": np.full(n, 0.12, "float32"),
        }
    )


def _render_name(d):
    return os.path.basename(os.path.normpath(d)) + "_render.png"


class TestSafeOversampling(unittest.TestCase):
    def test_clamps_to_max_edge(self):
        self.assertEqual(render_mod._safe_oversampling(8, 48, 4096), 8)
        # A 2048 px camera at 8x would be 16384 px -> clamp to 2x (<= 4096).
        self.assertEqual(render_mod._safe_oversampling(8, 2048, 4096), 2)
        # A falsy request floors at 1.
        self.assertEqual(render_mod._safe_oversampling(0, 48, 4096), 1)


class TestMergeInfo(unittest.TestCase):
    def test_pixelsize_required(self):
        self.assertIsNone(render_mod._merge_info(None, None, _locs()))

    def test_derives_width_height_from_locs(self):
        merged = render_mod._merge_info(None, 130.0, _locs(extent=40))
        self.assertEqual(merged["Pixelsize"], 130.0)
        self.assertGreater(merged["Width"], 0)
        self.assertGreater(merged["Height"], 0)

    def test_list_of_dicts_supplies_field(self):
        merged = render_mod._merge_info(
            [{"Width": 64, "Height": 48, "Pixelsize": 100.0}], None, _locs()
        )
        self.assertEqual((merged["Width"], merged["Height"]), (64, 48))


class TestSaveRenderPng(unittest.TestCase):
    def test_none_when_no_locs(self):
        self.assertIsNone(render_mod.save_render_png(None, "/x.png"))
        self.assertIsNone(
            render_mod.save_render_png(_locs(0), "/x.png", pixelsize_nm=130.0)
        )

    def test_none_when_no_pixelsize(self):
        self.assertIsNone(render_mod.save_render_png(_locs(), "/x.png"))

    def test_writes_png_with_expected_disp_px(self):
        captured = {}

        def fake_render(locs, info, *, disp_px_size, **kw):
            captured["disp_px_size"] = disp_px_size
            captured["info"] = info
            return len(locs), np.ones((64, 64), dtype="float32")

        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.png")
            with patch("picasso.render.render", fake_render):
                path = render_mod.save_render_png(
                    _locs(), out, pixelsize_nm=130.0, oversampling=8
                )
            self.assertEqual(path, out)
            self.assertTrue(os.path.exists(out))
        # disp_px_size = pixelsize / oversampling (no clamp at a 48 px field).
        self.assertAlmostEqual(captured["disp_px_size"], 130.0 / 8)
        self.assertEqual(captured["info"]["Pixelsize"], 130.0)


class TestRenderOutputPath(unittest.TestCase):
    def test_dir_source(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                LiveAnalysisService._render_output_path(
                    FovConfig(movie_source_path=d)
                ),
                os.path.join(d, _render_name(d)),
            )

    def test_file_source_uses_stem(self):
        cfg = FovConfig(movie_source_path="/data/run/movie.ome.tif")
        self.assertEqual(
            LiveAnalysisService._render_output_path(cfg),
            "/data/run/movie.ome_render.png",
        )

    def test_archive_fallback(self):
        cfg = FovConfig(archive_dir="/arch")
        self.assertEqual(
            LiveAnalysisService._render_output_path(cfg),
            os.path.join("/arch", "fov_render.png"),
        )

    def test_none_when_no_paths(self):
        self.assertIsNone(LiveAnalysisService._render_output_path(FovConfig()))


class TestMaybeRender(unittest.TestCase):
    def _metrics(self):
        m = RunningMetrics()
        m.update(_locs(), 50)
        return m

    def test_disabled_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = FovConfig(
                save_render=False, movie_source_path=d, pixelsize_nm=130.0
            )
            self.assertIsNone(
                LiveAnalysisService()._maybe_render(cfg, self._metrics())
            )

    def test_no_locs_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = FovConfig(
                save_render=True, movie_source_path=d, pixelsize_nm=130.0
            )
            self.assertIsNone(
                LiveAnalysisService()._maybe_render(cfg, RunningMetrics())
            )

    def test_calls_save_render_with_accumulated_locs(self):
        seen = {}

        def fake_save(locs, out_path, **kw):
            seen["n"] = len(locs)
            seen["px"] = kw.get("pixelsize_nm")
            with open(out_path, "wb") as f:
                f.write(b"png")
            return out_path

        with tempfile.TemporaryDirectory() as d:
            cfg = FovConfig(
                save_render=True, movie_source_path=d, pixelsize_nm=130.0
            )
            with patch(
                "PycroFlow.live_analysis.render.save_render_png", fake_save
            ):
                path = LiveAnalysisService()._maybe_render(
                    cfg, self._metrics()
                )
        self.assertEqual(path, os.path.join(d, _render_name(d)))
        self.assertGreater(seen["n"], 0)
        self.assertEqual(seen["px"], 130.0)

    def test_failure_is_swallowed(self):
        def boom(*a, **k):
            raise RuntimeError("render went wrong")

        with tempfile.TemporaryDirectory() as d:
            cfg = FovConfig(
                save_render=True, movie_source_path=d, pixelsize_nm=130.0
            )
            with patch("PycroFlow.live_analysis.render.save_render_png", boom):
                self.assertIsNone(
                    LiveAnalysisService()._maybe_render(cfg, self._metrics())
                )


class TestBuildFovConfigRender(unittest.TestCase):
    def test_defaults_on_and_overridable(self):
        cfg = build_fov_config({}, camera_info={"Pixelsize": 130.0})
        self.assertTrue(cfg.save_render)
        self.assertEqual(cfg.render_oversampling, 8)
        cfg2 = build_fov_config(
            {"save_render": False, "render_oversampling": 4},
            camera_info={},
        )
        self.assertFalse(cfg2.save_render)
        self.assertEqual(cfg2.render_oversampling, 4)


if __name__ == "__main__":
    unittest.main()
