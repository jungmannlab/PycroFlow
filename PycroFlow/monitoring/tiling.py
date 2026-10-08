"""Compose per-camera frames into a single fixed-size tile.

One tiled clip is written per round (the layout chosen for this WP), so the
compositor lays each camera's latest frame into a grid panel. A camera with no
current frame -- slow, dropped, or downed/unplugged -- renders as a black
panel (a visible gap) rather than stalling or corrupting the tile; that is the
panel-level expression of the isolation invariant.

The tile geometry is computed once when a clip opens and stays constant for the
whole clip, as the AVI writer requires a fixed frame size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from PycroFlow.monitoring.config import CameraConfig


@dataclass(frozen=True)
class TileLayout:
    """Fixed grid geometry for a tiled clip."""

    cols: int
    rows: int
    panel_w: int
    panel_h: int

    @property
    def width(self) -> int:
        return self.cols * self.panel_w

    @property
    def height(self) -> int:
        return self.rows * self.panel_h


def plan_layout(
    cameras: Sequence[CameraConfig], tile_cols: Optional[int] = None
) -> TileLayout:
    """Choose a grid + uniform panel size for ``cameras``.

    Panels are sized to the largest camera so no frame is cropped; smaller
    frames sit top-left in their panel. Columns default to ``ceil(sqrt(n))``.
    """
    n = max(1, len(cameras))
    cols = tile_cols or max(1, int(math.ceil(math.sqrt(n))))
    cols = min(cols, n)
    rows = int(math.ceil(n / cols))
    panel_w = max((c.width for c in cameras), default=640)
    panel_h = max((c.height for c in cameras), default=480)
    return TileLayout(cols=cols, rows=rows, panel_w=panel_w, panel_h=panel_h)


def compose(
    frames: Sequence[Optional[np.ndarray]], layout: TileLayout
) -> np.ndarray:
    """Lay ``frames`` into the tile; ``None`` (or wrong-shaped) => black panel.

    Parameters
    ----------
    frames : sequence of (ndarray or None)
        One entry per camera, in camera order. ``None`` marks a missing frame.
    layout : TileLayout
        Geometry from :func:`plan_layout`.

    Returns
    -------
    numpy.ndarray
        The tiled ``HxWx3`` RGB uint8 frame.
    """
    tile = np.zeros((layout.height, layout.width, 3), dtype=np.uint8)
    for i, frame in enumerate(frames):
        if frame is None:
            continue
        r, c = divmod(i, layout.cols)
        y0, x0 = r * layout.panel_h, c * layout.panel_w
        h = min(frame.shape[0], layout.panel_h)
        w = min(frame.shape[1], layout.panel_w)
        tile[y0 : y0 + h, x0 : x0 + w] = frame[:h, :w]
    return tile
