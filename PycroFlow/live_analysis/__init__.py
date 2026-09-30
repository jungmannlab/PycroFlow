"""WP-4 live analysis: acquire one FOV, live-localize (no drift), one record.

The first end-to-end slice of the automation loop. A frontend-agnostic
:class:`~PycroFlow.live_analysis.service.LiveAnalysisService` owns the
acquisition + a ``frames -> locs`` pipeline and pushes state over a thin client
seam (in-process Qt signals today, shaped so a WebSocket/SSE + REST client is a
later drop-in). It never blocks acquisition: a pool of worker processes runs
picasso's ``localize_frames`` off a bounded queue, and if it cannot keep pace it
LAGS in contiguous batches rather than subsampling the authoritative stream.

Public surface is imported lazily by frontends; importing this package must not
require pycromanager / picasso / PyQt6 (they load on first use), so it is
import-safe on dev / CI.
"""

from __future__ import annotations

from PycroFlow.live_analysis.frame_source import (
    Batch,
    FrameSource,
    MockFrameSource,
    RamPeekFrameSource,
    TiffTailFrameSource,
    make_frame_source,
    natural_key,
    position_from_name,
)
from PycroFlow.live_analysis.metrics import RunningMetrics
from PycroFlow.live_analysis.run_id import new_run_id

__all__ = [
    "Batch",
    "FrameSource",
    "MockFrameSource",
    "RamPeekFrameSource",
    "TiffTailFrameSource",
    "make_frame_source",
    "natural_key",
    "position_from_name",
    "RunningMetrics",
    "new_run_id",
]
