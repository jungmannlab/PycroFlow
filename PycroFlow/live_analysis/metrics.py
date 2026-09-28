"""Running live-analysis metrics over the authoritative localization stream.

As contiguous batches of localizations arrive (from picasso ``localize_frames``
over the lossless frame stream), :class:`RunningMetrics` accumulates them and
exposes the cheap live observables the Quality tab trends and the per-FOV
registry record snapshots: **localizations/frame**, **background**, and **NeNA**
(experimental localization precision). No live drift correction is done on the
authoritative path (that is whole-dataset work on the cluster, WP downstream).

NeNA is computed by picasso's own ``postprocess.nena`` on the accumulated table
so the live number equals the batch/offline number over the same frames (the T2
oracle). It is recomputed on a throttle (every ``nena_min_new_locs`` new locs
and at most every ``nena_min_interval_s``) because it is O(n) — a live trend, not
a per-batch cost. The final per-FOV snapshot always forces a fresh NeNA.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


class RunningMetrics:
    """Accumulate localizations and expose live NeNA / locs-per-frame / bg.

    Thread-safe: :meth:`update` (from the pipeline drain) and :meth:`snapshot`
    (from a client / the final record) may be called concurrently.

    Parameters
    ----------
    nena_min_new_locs : int
        Recompute NeNA only after at least this many new localizations.
    nena_min_interval_s : float
        And at least this many seconds since the last NeNA compute.
    """

    def __init__(
        self,
        *,
        nena_min_new_locs: int = 2000,
        nena_min_interval_s: float = 2.0,
    ):
        self._lock = threading.Lock()
        self._chunks: list["pd.DataFrame"] = []
        self._locs: "pd.DataFrame | None" = None
        self._n_locs = 0
        self._n_frames_covered = 0
        self._bg_sum = 0.0
        self._bg_count = 0
        self._nena_min_new = nena_min_new_locs
        self._nena_min_interval = nena_min_interval_s
        self._nena_nm: float | None = None
        self._nena_px: float | None = None
        self._last_nena_at = 0.0
        self._locs_at_last_nena = 0
        self._pixelsize_nm: float | None = None
        # picasso metadata (list-of-dicts). NeNA needs a dict / list-of-dicts —
        # ``postprocess.nena`` looks up ``Pixelsize`` from it (passing ``None``
        # raises ``ValueError: info must be a dict or a list of dicts``), so we
        # keep the camera info here and hand it to every NeNA call.
        self._info: list | None = None

    def set_pixelsize_nm(self, pixelsize_nm: float | None) -> None:
        """Set the camera pixel size (nm), used to report NeNA in nm."""
        with self._lock:
            self._pixelsize_nm = pixelsize_nm

    def set_info(self, info) -> None:
        """Set the picasso metadata used by NeNA (a dict or list-of-dicts).

        Normalised to a list-of-dicts (picasso's convention). If it carries a
        ``Pixelsize`` and no explicit pixel size was set, that is adopted so
        NeNA can be reported in nm.
        """
        with self._lock:
            if info is None:
                self._info = None
                return
            info_list = [info] if isinstance(info, dict) else list(info)
            self._info = info_list
            if self._pixelsize_nm is None:
                for inf in info_list:
                    if isinstance(inf, dict) and inf.get("Pixelsize"):
                        self._pixelsize_nm = float(inf["Pixelsize"])
                        break

    def update(self, locs: "pd.DataFrame", n_frames_in_batch: int) -> None:
        """Fold one batch's localizations + its frame count into the running set.

        Parameters
        ----------
        locs : pandas.DataFrame
            The batch's localizations (from ``localize_frames``), frame indices
            already absolute (``start_frame`` offset applied by the caller).
        n_frames_in_batch : int
            Frames covered by this batch — added to the authoritative frame
            count so localizations/frame divides by every frame, never a subset.
        """
        with self._lock:
            self._chunks.append(locs)
            self._locs = None  # invalidate the concatenation cache
            self._n_locs += len(locs)
            self._n_frames_covered += n_frames_in_batch
            if "bg" in locs.columns and len(locs):
                self._bg_sum += float(locs["bg"].sum())
                self._bg_count += len(locs)

    def _concat(self) -> "pd.DataFrame | None":
        if self._locs is None and self._chunks:
            import pandas as pd

            self._locs = pd.concat(self._chunks, ignore_index=True)
        return self._locs

    def _maybe_nena(self, force: bool) -> None:
        """(Re)compute NeNA under the throttle, or unconditionally if ``force``."""
        if self._n_locs < 100:
            return
        new = self._n_locs - self._locs_at_last_nena
        due = (
            new >= self._nena_min_new
            and (time.monotonic() - self._last_nena_at)
            >= self._nena_min_interval
        )
        if not force and not due:
            return
        locs = self._concat()
        if locs is None or len(locs) < 100:
            return
        try:
            from picasso import postprocess

            # nena requires info to be a dict / list-of-dicts (it reads
            # Pixelsize from it); fall back to an empty dict when we have none
            # so the call never raises just for lack of metadata.
            info = self._info if self._info is not None else {}
            _result, s_px = postprocess.nena(locs, info)
        except Exception:
            return
        self._nena_px = float(s_px)
        if self._pixelsize_nm:
            self._nena_nm = float(s_px) * float(self._pixelsize_nm)
        else:
            self._nena_nm = None
        self._last_nena_at = time.monotonic()
        self._locs_at_last_nena = self._n_locs

    def snapshot(self, *, force_nena: bool = False) -> dict:
        """Return the current live metrics as a plain dict.

        Parameters
        ----------
        force_nena : bool
            Recompute NeNA now regardless of the throttle — the final per-FOV
            snapshot forces it so the record reflects every frame read.
        """
        with self._lock:
            self._maybe_nena(force=force_nena)
            n_frames = self._n_frames_covered
            locs_per_frame = (self._n_locs / n_frames) if n_frames else 0.0
            background = (
                self._bg_sum / self._bg_count if self._bg_count else None
            )
            return {
                "n_locs": self._n_locs,
                "n_frames": n_frames,
                "spots_per_frame": round(locs_per_frame, 6),
                "background": (
                    round(background, 4) if background is not None else None
                ),
                "nena_nm": (
                    round(self._nena_nm, 4)
                    if self._nena_nm is not None
                    else None
                ),
                "nena_px": (
                    round(self._nena_px, 6)
                    if self._nena_px is not None
                    else None
                ),
            }
