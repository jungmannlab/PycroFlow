"""Align a run's webcam clips to the replay timeline and seek frames.

The fluidics-monitoring webcam writes one tiled ``.avi`` clip per exchange
round (:mod:`PycroFlow.monitoring.capture_service`), each filename carrying a
wall-clock UTC start stamp. :class:`WebcamReplaySource` discovers those clips
for a finished run, maps each clip's wall-clock start to a run-relative
millisecond position (using the replay timeline's ``t0_utc`` anchor), and —
given a scrub position — returns the composite frame that was on screen then.

Frames are decoded with the pure-Python AVI reader
(:func:`PycroFlow.monitoring.avi.read_avi`), so no OpenCV is required; one clip
is cached at a time to bound memory while scrubbing within a clip stays cheap.
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger

# run_<id>_round<NNN>_fluid-step<...>_<YYYYmmddTHHMMSSZ>.avi — the trailing
# stamp is the clip's wall-clock start; the round index is the `roundNNN` group.
_CLIP_RE = re.compile(r"_round(?P<round>\d+)_.*_(?P<utc>\d{8}T\d{6}Z)\.avi$")
_UTC_FMT = "%Y%m%dT%H%M%SZ"


def _parse_utc_epoch(stamp: str) -> Optional[float]:
    try:
        dt = datetime.strptime(stamp, _UTC_FMT).replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


class _Clip:
    __slots__ = ("round_index", "start_ms", "path", "end_ms")

    def __init__(self, round_index, start_ms, path):
        self.round_index = round_index
        self.start_ms = start_ms
        self.path = path
        self.end_ms = None  # filled in once the clip order is known


class WebcamReplaySource:
    """Resolve the webcam frame on screen at a given replay time.

    Parameters
    ----------
    clips : list of _Clip
        Discovered clips with their timeline start (ms). Normally built via
        :meth:`from_dir`.
    fps : float
        Capture frame rate (``MonitoringConfig.fps``), used to size the last
        clip's window and to map a within-clip time to a frame index.
    """

    def __init__(self, clips: List["_Clip"], fps: float = 5.0):
        self._fps = max(1.0, float(fps or 5.0))
        self._clips = sorted(clips, key=lambda c: c.start_ms)
        self._cache_path = None
        self._cache_frames = None
        # Each clip is active until the next clip starts; the last one for its
        # own decoded length (filled lazily on first read when unknown).
        for i, clip in enumerate(self._clips[:-1]):
            clip.end_ms = self._clips[i + 1].start_ms

    @classmethod
    def from_dir(
        cls, clip_dir: str, t0_utc: float, fps: float = 5.0
    ) -> "WebcamReplaySource":
        """Discover a run's clips in ``clip_dir`` and anchor them to ``t0_utc``.

        Returns a source with no clips (a harmless no-op) when the directory is
        absent / empty, ``t0_utc`` is unknown, or no filename parses.
        """
        clips: List[_Clip] = []
        if clip_dir and t0_utc and os.path.isdir(clip_dir):
            for path in glob.glob(os.path.join(clip_dir, "*_round*.avi")):
                m = _CLIP_RE.search(os.path.basename(path))
                if not m:
                    continue
                epoch = _parse_utc_epoch(m.group("utc"))
                if epoch is None:
                    continue
                start_ms = max(0, int(round((epoch - t0_utc) * 1000.0)))
                clips.append(_Clip(int(m.group("round")), start_ms, path))
        return cls(clips, fps=fps)

    @property
    def clip_count(self) -> int:
        return len(self._clips)

    def _active_clip(self, t_ms: int):
        """The last clip whose start is at or before ``t_ms`` (or None)."""
        active = None
        for clip in self._clips:
            if clip.start_ms <= t_ms:
                active = clip
            else:
                break
        return active

    def _frames_for(self, clip: "_Clip"):
        """Decode (and cache) a clip's frames; returns a list (empty on error)."""
        if self._cache_path == clip.path and self._cache_frames is not None:
            return self._cache_frames
        from PycroFlow.monitoring import avi

        try:
            frames = avi.read_avi(clip.path)
        except Exception as exc:  # noqa: BLE001 - replay is best-effort
            logger.warning(
                "replay: could not read clip {}: {!r}", clip.path, exc
            )
            frames = []
        self._cache_path = clip.path
        self._cache_frames = frames
        return frames

    def frame_at(self, t_ms: int):
        """The composite RGB frame (H, W, 3) on screen at ``t_ms``, or None.

        Picks the active clip, then the frame whose offset into the clip
        matches ``t_ms`` (at the capture fps), clamped to the clip's bounds. A
        time in a gap between clips freezes on the previous clip's last frame.
        """
        clip = self._active_clip(t_ms)
        if clip is None:
            return None
        frames = self._frames_for(clip)
        if not frames:
            return None
        offset_s = max(0, t_ms - clip.start_ms) / 1000.0
        idx = int(round(offset_s * self._fps))
        idx = min(max(idx, 0), len(frames) - 1)
        return frames[idx]

    def close(self) -> None:
        self._cache_path = None
        self._cache_frames = None
