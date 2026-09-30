"""The ``frames -> locs`` unit of work: run picasso ``localize_frames``.

Isolated here so it can run in a worker *process* (the pool ships the frame
stack + params to a subprocess via a multiprocessing queue) and be unit-tested
directly. picasso is imported lazily inside :func:`localize_batch` so this module
imports on machines/CI without a GPU-built picasso.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LocalizeRequest:
    """One batch of frames to localize (what the pool hands a worker).

    Attributes
    ----------
    seq : int
        Monotonic batch sequence number (for ordering / bookkeeping).
    frames : any
        In-memory ``(n, h, w)`` frame stack.
    start_frame : int
        Absolute index of the first frame — passed to ``localize_frames`` so the
        returned ``frame`` column is absolute and contiguous across batches.
    position : int | None
        Stage-position index this batch belongs to (never mixed within a batch).
    """

    seq: int
    frames: object
    start_frame: int
    position: int | None


@dataclass
class LocalizeResult:
    """A worker's output for one batch."""

    seq: int
    locs: object  # pandas.DataFrame
    n_frames: int
    start_frame: int
    position: int | None
    error: str | None = None


def localize_batch(
    frames,
    info,
    params: dict,
    *,
    start_frame: int = 0,
    fitting_method: str = "gausslq",
):
    """Localize one in-memory frame stack via picasso ``localize_frames``.

    Thin, GUI-free wrapper: returns exactly what ``localize_frames`` returns (a
    pandas DataFrame), so live localizations match the offline path spot-for-spot
    on the same frames + params (the T2 oracle relies on this).

    Parameters
    ----------
    frames : array-like
        ``(n, h, w)`` stack.
    info : list of dict or None
        Picasso info list-of-dicts (movie/camera metadata).
    params : dict
        Identification parameters — at least ``"Min. Net Gradient"`` and
        ``"Box Size"``.
    start_frame : int
        Absolute index of the first frame.
    fitting_method : str
        picasso fitting method (``"gausslq"``; ``*-gpu`` variants use the GPU).
    """
    from picasso import localize as _localize

    info_list = [info] if isinstance(info, dict) else info
    return _localize.localize_frames(
        frames,
        info_list,
        params,
        start_frame=start_frame,
        fitting_method=fitting_method,
    )


def _worker_main(
    in_q, out_q, info, params: dict, fitting_method: str
):  # pragma: no cover - runs in a child process
    """Worker-process loop: pull :class:`LocalizeRequest`, push results.

    A ``None`` sentinel on the input queue ends the worker. Errors are captured
    into the result (never crash the pool) so the parent can log + continue.
    """
    while True:
        req = in_q.get()
        if req is None:
            break
        try:
            locs = localize_batch(
                req.frames,
                info,
                params,
                start_frame=req.start_frame,
                fitting_method=fitting_method,
            )
            out_q.put(
                LocalizeResult(
                    seq=req.seq,
                    locs=locs,
                    n_frames=int(req.frames.shape[0]),
                    start_frame=req.start_frame,
                    position=req.position,
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported, worker survives
            out_q.put(
                LocalizeResult(
                    seq=req.seq,
                    locs=None,
                    n_frames=int(getattr(req.frames, "shape", [0])[0]),
                    start_frame=req.start_frame,
                    position=req.position,
                    error=repr(exc),
                )
            )
