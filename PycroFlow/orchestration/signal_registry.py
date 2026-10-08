"""Event-backed signal registry replacing the list-of-strings + busy-poll.

The pre-Stage-4 implementation (``orchestration.core.AbstractSystemHandler.
wait_xchange``) polled a per-subsystem ``list[str]`` every 50 ms looking
for the awaited signal. Stage 1 added a hard timeout so a typo could no
longer hang forever, but it still burned CPU and missed signals if the
poll caught the list mid-mutation under contention.

:class:`SignalRegistry` keeps a per-``(target, value)`` ``threading.Event``.
Producers fire the event; waiters call ``Event.wait(timeout=)`` which
returns the instant the event is set with no polling. The same instance
is shared across all handlers via :class:`ThreadExchange`.

The legacy list-of-strings path stays alive in parallel for back-compat:
some code uses search_message() to find substring-prefixed entries (e.g.
'start entry: 3'), which this registry cannot represent. The handler
path tries the registry first (the fast path), then falls back to the
list.

Thread-safety: ``threading.Event`` and the dict-mutation operations are
already atomic in CPython for our access patterns; the explicit internal
lock guards the dict membership check + insertion that ``register`` and
``fire`` perform when a signal is fired before anyone has registered it.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from loguru import logger

# An observer is notified of every fired signal as ``fn(target, value)``. Used
# by the fluidics monitoring subsystem to bind camera-record windows to the
# round lifecycle without the orchestration knowing anything about it.
SignalObserver = Callable[[str, str], None]


def _key(target: str, value: str) -> str:
    """Combine target + value into a single registry key."""
    return f"{target}::{value}"


class SignalRegistry:
    """Per-signal ``threading.Event`` registry.

    A signal is identified by a ``(target, value)`` pair. Producers call
    :meth:`fire` to mark it observed; consumers call :meth:`wait` to block
    until then (or the timeout expires).
    """

    def __init__(self):
        self._events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._observers: List[SignalObserver] = []

    def add_observer(self, fn: SignalObserver) -> None:
        """Register a callback fired on every :meth:`fire`, as ``fn(target,
        value)``.

        Observers run inline on the firing (orchestration) thread, so a
        callback **must not block** -- it should hand off and return at once.
        Exceptions are caught and logged: an observer can never break signal
        delivery or the run. Used by the monitoring subsystem to detect round
        boundaries; see :class:`PycroFlow.monitoring.controller.
        MonitoringController`.
        """
        self._observers.append(fn)

    def remove_observer(self, fn: SignalObserver) -> None:
        """Deregister an observer added by :meth:`add_observer` (no-op if
        absent)."""
        try:
            self._observers.remove(fn)
        except ValueError:
            pass

    def _get_or_create(self, target: str, value: str) -> threading.Event:
        k = _key(target, value)
        # Common-case fast path: read without the lock. Lock only on the
        # rare miss to avoid double-insertion.
        ev = self._events.get(k)
        if ev is not None:
            return ev
        with self._lock:
            ev = self._events.get(k)
            if ev is None:
                ev = threading.Event()
                self._events[k] = ev
            return ev

    def fire(self, target: str, value: str) -> None:
        """Mark ``(target, value)`` observed. Idempotent — re-firing is a
        no-op once the event is set."""
        self._get_or_create(target, value).set()
        # Notify observers best-effort: never let a monitoring hook (or any
        # other subscriber) break signal delivery to the waiting handlers.
        # Iterate a snapshot (like ExperimentService._set_state) so concurrent
        # add/remove_observer can't skip a callback mid-iteration.
        for fn in list(self._observers):
            try:
                fn(target, value)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("signal observer raised: {!r}", exc)

    def wait(
        self, target: str, value: str, timeout: Optional[float] = None
    ) -> bool:
        """Block until ``(target, value)`` fires or ``timeout`` elapses.

        Returns True iff the signal fired before the timeout. Caller is
        responsible for raising whatever exception is appropriate (the
        orchestration code raises ``WaitForSignalTimeout`` on False).
        """
        return self._get_or_create(target, value).wait(timeout=timeout)

    def is_set(self, target: str, value: str) -> bool:
        """Non-blocking poll."""
        ev = self._events.get(_key(target, value))
        return ev is not None and ev.is_set()

    def clear(self, target: str, value: str) -> None:
        """Reset the event so a subsequent wait blocks again. Useful in
        tests and in re-entrant protocols that loop through the same
        signal name multiple times."""
        ev = self._events.get(_key(target, value))
        if ev is not None:
            ev.clear()

    def reset(self) -> None:
        """Drop all signals. Used between protocol runs in tests."""
        with self._lock:
            self._events.clear()
