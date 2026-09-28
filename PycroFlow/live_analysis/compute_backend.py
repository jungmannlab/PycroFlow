"""Pluggable ``frames -> locs`` compute backend behind the frame stream.

The service reads batches from a :class:`~PycroFlow.live_analysis.frame_source.
FrameSource` and hands them to a compute backend that turns frames into
localizations. Two backends behind one interface, so switching *where* the
localization runs is config, not a rewrite:

* :class:`LocalComputeBackend` — **ships now, the default**. A POOL OF WORKER
  PROCESSES runs picasso ``localize_frames`` (per WP-1 a separate process avoids
  contending with acquisition's GIL / ZMQ bridge). Batches flow through a
  BOUNDED input queue; a drain thread collects results and feeds the running
  metrics. Backpressure: for the *lossless authoritative* stream the backend
  **lags** — ``submit`` blocks when the queue is full, which slows *reading off
  disk*, never acquisition (frames wait on disk), and NEVER subsamples. It
  reads LOCAL disk (the fallback path); the movie write-target and archive are
  handled by the service.

* :class:`RemoteComputeBackend` — **stubbed** seam for a LAN GPU node that reads
  the POOL folder and localizes live (register C33 topology, gated on decision
  **B5**). The interface is identical so the switch is a config flag; the
  transport is intentionally not built here.

A runtime backpressure invariant (bounded queue never silently grows; the drop
policy is a real, asserted decision — not just a test) lives in
:class:`_BoundedSubmitQueue`.
"""

from __future__ import annotations

import abc
import multiprocessing as mp
import queue as _queue
import threading
import time
from typing import Callable

from loguru import logger

from PycroFlow.live_analysis.worker import (
    LocalizeRequest,
    LocalizeResult,
    _worker_main,
    localize_batch,
)

# Backpressure policy for the authoritative (lossless) stream. We LAG rather
# than drop: submission blocks until the pool drains, which slows the disk
# reader — never the producer/acquisition (frames are already safe on disk).
POLICY_LAG = "lag"
# For a lossy live-view stream a caller may choose to DROP the oldest queued
# batch instead (fresh-first), like the RAM-peek source does upstream.
POLICY_DROP_OLDEST = "drop-oldest"


class _BoundedSubmitQueue:
    """A bounded queue enforcing the backpressure policy AT RUNTIME.

    This is the production-code assertion the Tier-3 test extends: the queue can
    never exceed ``maxsize`` (so memory can't blow up and acquisition can't be
    starved by an unbounded backlog), and the policy — lag (block) or drop — is
    applied here, not left implicit. ``depth`` is observable so the service /
    Quality tab can surface live lag.
    """

    def __init__(self, maxsize: int, policy: str):
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if policy not in (POLICY_LAG, POLICY_DROP_OLDEST):
            raise ValueError("unknown policy {!r}".format(policy))
        self._q: "_queue.Queue" = _queue.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._policy = policy
        self.n_dropped = 0
        self.max_depth_seen = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def depth(self) -> int:
        return self._q.qsize()

    def put(self, item, *, block_timeout: float = 0.1) -> bool:
        """Enqueue one item under the policy. Returns True if it was enqueued.

        LAG: block until space frees (in ``block_timeout`` slices so a stop can
        interrupt). DROP_OLDEST: evict the oldest to make room, count the drop.
        Runtime invariant: the queue never exceeds ``maxsize``.
        """
        if self._policy == POLICY_LAG:
            while True:
                try:
                    self._q.put(item, timeout=block_timeout)
                    self._note_depth()
                    return True
                except _queue.Full:
                    # Keep lagging; the caller's stop flag breaks the outer loop.
                    return False
        # DROP_OLDEST
        try:
            self._q.put_nowait(item)
        except _queue.Full:
            try:
                self._q.get_nowait()
                self.n_dropped += 1
            except _queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except _queue.Full:
                self.n_dropped += 1
                return False
        self._note_depth()
        return True

    def get(self, timeout: float | None = None):
        return self._q.get(timeout=timeout)

    def _note_depth(self) -> None:
        depth = self._q.qsize()
        # The runtime backpressure invariant: a bounded queue, always.
        assert depth <= self._maxsize, (
            "submit queue overflow: depth {} > maxsize {} — backpressure "
            "policy failed".format(depth, self._maxsize)
        )
        if depth > self.max_depth_seen:
            self.max_depth_seen = depth


class ComputeBackend(abc.ABC):
    """Turn frame batches into localizations, feeding a result callback.

    Lifecycle: :meth:`start` (spin up workers), :meth:`submit` per batch (may
    block under the lag policy), :meth:`drain_and_stop` (finish in-flight work,
    tear down). Results are delivered to the callback passed at construction:
    ``on_result(LocalizeResult)`` — called on the backend's drain thread.
    """

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def submit(self, request: LocalizeRequest) -> bool:
        """Submit one batch. Returns False if it could not be enqueued (stopping).

        Under :data:`POLICY_LAG` this BLOCKS while the pool is saturated (that's
        the lag); it never subsamples the authoritative stream.
        """

    @abc.abstractmethod
    def drain_and_stop(self, timeout: float = 60.0) -> None: ...

    @abc.abstractmethod
    def stats(self) -> dict: ...


class LocalComputeBackend(ComputeBackend):
    """Pool of worker PROCESSES running ``localize_frames`` on local disk frames.

    Parameters
    ----------
    info : list of dict or dict or None
        Picasso info/camera metadata handed to each worker once.
    params : dict
        Identification parameters (``"Min. Net Gradient"``, ``"Box Size"``, …).
    on_result : callable
        ``on_result(LocalizeResult)`` invoked per finished batch (drain thread).
    n_workers : int
        Worker processes in the pool.
    queue_size : int
        Bounded submit-queue depth; the lag/drop policy triggers when full.
    policy : str
        :data:`POLICY_LAG` (authoritative, default) or :data:`POLICY_DROP_OLDEST`.
    fitting_method : str
        picasso fitting method for the workers.
    use_processes : bool
        True (default) spawns worker processes (WP-1 isolation). False runs the
        localize call inline on the drain thread — used by hermetic tests so no
        child process / picasso import is required.
    """

    def __init__(
        self,
        *,
        info,
        params: dict,
        on_result: Callable[[LocalizeResult], None],
        n_workers: int = 2,
        queue_size: int = 8,
        policy: str = POLICY_LAG,
        fitting_method: str = "gausslq",
        use_processes: bool = True,
    ):
        self._info = info
        self._params = params
        self._on_result = on_result
        self._n_workers = max(1, n_workers)
        self._policy = policy
        self._fitting_method = fitting_method
        self._use_processes = use_processes
        self._submit_q = _BoundedSubmitQueue(queue_size, policy)
        self._stop = threading.Event()
        self._procs: list = []
        self._result_q = None
        self._feeder: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._submitted = 0
        self._completed = 0
        self._errors = 0
        self._inflight = threading.Semaphore(0)  # counts pending results

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._use_processes:
            ctx = mp.get_context("spawn")
            self._result_q = ctx.Queue()
            self._proc_in_q = ctx.Queue(maxsize=self._submit_q.maxsize)
            for i in range(self._n_workers):
                p = ctx.Process(
                    target=_worker_main,
                    args=(
                        self._proc_in_q,
                        self._result_q,
                        self._info,
                        self._params,
                        self._fitting_method,
                    ),
                    name="live-localize-{}".format(i),
                    daemon=True,
                )
                p.start()
                self._procs.append(p)
            # A feeder thread moves items from the policy-bounded submit queue
            # into the process input queue, so the policy (lag/drop) is enforced
            # in-process before crossing the process boundary.
            self._feeder = threading.Thread(
                target=self._feed_processes, name="live-feeder", daemon=True
            )
            self._feeder.start()
            self._drain = threading.Thread(
                target=self._drain_processes, name="live-drain", daemon=True
            )
            self._drain.start()
        else:
            # In-process fallback: a single thread pops the submit queue and
            # localizes inline (used by hermetic tests).
            self._drain = threading.Thread(
                target=self._drain_inline, name="live-drain", daemon=True
            )
            self._drain.start()

    def submit(self, request: LocalizeRequest) -> bool:
        if self._stop.is_set():
            return False
        # Retry under the lag policy until enqueued or a stop is requested.
        while not self._stop.is_set():
            ok = self._submit_q.put(request)
            if ok:
                self._submitted += 1
                return True
            if self._policy != POLICY_LAG:
                return False
            # lag: loop; put() returned False only on its inner Full timeout.
        return False

    def drain_and_stop(self, timeout: float = 60.0) -> None:
        # Signal end-of-input, let the feeder/drain finish in-flight work.
        deadline = time.monotonic() + timeout
        # Wait for the submit queue to empty (all handed to workers).
        while self._submit_q.depth() > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        if self._use_processes:
            # Wait for outstanding results, then stop workers.
            while (
                self._completed < self._submitted
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self._stop.set()
            for _ in self._procs:
                try:
                    self._proc_in_q.put(None)
                except Exception:
                    pass
            for p in self._procs:
                p.join(timeout=max(0.0, deadline - time.monotonic()))
                if p.is_alive():  # pragma: no cover - defensive
                    p.terminate()
        else:
            while (
                self._completed < self._submitted
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self._stop.set()
        if self._drain is not None:
            self._drain.join(timeout=5.0)
        if self._feeder is not None:
            self._feeder.join(timeout=5.0)

    # -- internals ----------------------------------------------------------
    def _feed_processes(self) -> None:  # pragma: no cover - process path
        while not self._stop.is_set():
            try:
                item = self._submit_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            self._proc_in_q.put(item)

    def _drain_processes(self) -> None:  # pragma: no cover - process path
        while not (self._stop.is_set() and self._completed >= self._submitted):
            try:
                res = self._result_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            self._handle_result(res)

    def _drain_inline(self) -> None:
        while not (self._stop.is_set() and self._completed >= self._submitted):
            try:
                req = self._submit_q.get(timeout=0.05)
            except _queue.Empty:
                continue
            try:
                locs = localize_batch(
                    req.frames,
                    self._info,
                    self._params,
                    start_frame=req.start_frame,
                    fitting_method=self._fitting_method,
                )
                res = LocalizeResult(
                    seq=req.seq,
                    locs=locs,
                    n_frames=int(req.frames.shape[0]),
                    start_frame=req.start_frame,
                    position=req.position,
                )
            except Exception as exc:  # noqa: BLE001
                res = LocalizeResult(
                    seq=req.seq,
                    locs=None,
                    n_frames=int(req.frames.shape[0]),
                    start_frame=req.start_frame,
                    position=req.position,
                    error=repr(exc),
                )
            self._handle_result(res)

    def _handle_result(self, res: LocalizeResult) -> None:
        self._completed += 1
        if res.error is not None:
            self._errors += 1
            logger.warning(
                "live-localize batch {} failed: {}".format(res.seq, res.error)
            )
            return
        try:
            self._on_result(res)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a bad sink must not kill drain
            logger.warning(
                "live-analysis result sink raised: {!r}".format(exc)
            )

    def stats(self) -> dict:
        return {
            "backend": "local-subprocess" if self._use_processes else "inline",
            "n_workers": self._n_workers,
            "policy": self._policy,
            "queue_size": self._submit_q.maxsize,
            "queue_depth": self._submit_q.depth(),
            "queue_max_depth": self._submit_q.max_depth_seen,
            "submitted": self._submitted,
            "completed": self._completed,
            "errors": self._errors,
            "dropped": self._submit_q.n_dropped,
        }


class RemoteComputeBackend(ComputeBackend):
    """STUB seam for a LAN GPU node reading the pool folder (B5-gated).

    Intended default topology (register C33) once decision **B5** lands: the
    camera writes to the pool network folder and an always-on GPU node reads it
    and localizes live. Building the seam now (identical interface) means the
    switch from :class:`LocalComputeBackend` is a config flag, not a rewrite. The
    remote transport (a small RPC to the GPU node's live-localize service) is
    intentionally not implemented here.
    """

    def __init__(self, *, endpoint: str | None = None, **_ignored):
        self.endpoint = endpoint

    def _not_built(self):
        raise NotImplementedError(
            "RemoteComputeBackend is a B5-gated stub: the LAN GPU-node "
            "live-localize transport is not built yet. Use "
            "LocalComputeBackend (local-subprocess) until B5 lands."
        )

    def start(self) -> None:
        self._not_built()

    def submit(self, request: LocalizeRequest) -> bool:
        self._not_built()

    def drain_and_stop(self, timeout: float = 60.0) -> None:
        self._not_built()

    def stats(self) -> dict:
        return {"backend": "remote-worker", "built": False}


COMPUTE_LOCAL = "local-subprocess"
COMPUTE_REMOTE = "remote-worker"


def make_compute_backend(kind: str, **kwargs) -> ComputeBackend:
    """Construct the compute backend by name.

    ``"local-subprocess"`` ships now (default); ``"remote-worker"`` is the
    B5-gated stub. Unknown kinds raise so a typo can't silently pick the wrong
    backend.
    """
    if kind == COMPUTE_LOCAL:
        return LocalComputeBackend(**kwargs)
    if kind == COMPUTE_REMOTE:
        return RemoteComputeBackend(**kwargs)
    raise ValueError("unknown compute backend {!r}".format(kind))
