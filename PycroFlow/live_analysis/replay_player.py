"""Replay a recorded run timeline by driving the live widgets.

:class:`ReplayController` loads a ``*_replay.jsonl`` written by
:class:`~PycroFlow.live_analysis.replay_recorder.ReplayRecorder` and, on
:meth:`seek`, reconstructs the state of each stream *as of* a timeline position
and pushes it to injected sinks — the live-view panels (re-emitted as a
:class:`~PycroFlow.live_analysis.client_seam.LiveUpdate`, so the real
``OverviewZoom`` / ``QcAtAGlance`` render it), the fluidics schematic, and the
run-sequence progress highlight.

The controller is intentionally Qt-free (sinks are plain callables) so it unit-
tests headlessly; the Qt scrubber widget in
:mod:`PycroFlow.gui.live.replay_scrubber` owns the slider/play controls and
wires these sinks to the real widgets.
"""

from __future__ import annotations

import bisect
import json
from typing import Callable, Optional

from PycroFlow.live_analysis.client_seam import LiveUpdate
from PycroFlow.live_analysis.replay_recorder import decode_payload

# Kinds that reconstruct a LiveUpdate for the live-view panels.
_LIVE_KINDS = ("state", "metrics", "thumbnail", "log", "record")


class ReplayController:
    """Drive the live widgets from a recorded timeline.

    Parameters
    ----------
    events : list of dict
        The parsed timeline records (each ``{t_ms, kind, payload, run_id?}``).
    emit_update : callable, optional
        Sink for reconstructed :class:`LiveUpdate`\\ s (the live panels).
    set_progress : callable, optional
        Sink for ``{system: (cur, total)}`` progress snapshots.
    set_fluid_state : callable, optional
        Sink for fluidics-state snapshots (the schematic).
    """

    def __init__(
        self,
        events,
        *,
        emit_update: Optional[Callable[[LiveUpdate], None]] = None,
        set_progress: Optional[Callable[[dict], None]] = None,
        set_fluid_state: Optional[Callable[[dict], None]] = None,
        set_webcam: Optional[Callable[[int], None]] = None,
    ):
        self._events = sorted(events, key=lambda e: e.get("t_ms", 0))
        self._emit_update = emit_update
        self._set_progress = set_progress
        self._set_fluid_state = set_fluid_state
        # Webcam frames live in external .avi clips, not the timeline; this sink
        # is simply handed the scrub time and resolves/shows the frame itself.
        self._set_webcam = set_webcam
        # Per-kind (times, events) for last-event-<=-t lookups.
        self._by_kind: dict = {}
        self._t0_utc = None
        for ev in self._events:
            self._by_kind.setdefault(ev.get("kind"), []).append(ev)
            if ev.get("kind") == "meta":
                self._t0_utc = (ev.get("payload") or {}).get("t0_utc")

    @property
    def t0_utc(self):
        """Wall-clock epoch the run started (from the timeline's meta header),
        or None. Used to align webcam clips to the timeline."""
        return self._t0_utc

    def set_webcam_sink(self, fn: Optional[Callable[[int], None]]) -> None:
        """Attach (or clear) the webcam sink after construction.

        Webcam alignment needs :attr:`t0_utc`, which is only known once the
        timeline is parsed, so the main window wires this sink after loading.
        """
        self._set_webcam = fn

    @classmethod
    def from_file(cls, path: str, **sinks) -> "ReplayController":
        """Load a timeline JSONL file into a controller."""
        events = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue  # tolerate a truncated final line
        return cls(events, **sinks)

    @property
    def duration_ms(self) -> int:
        """Timeline length in ms (0 when empty)."""
        return self._events[-1].get("t_ms", 0) if self._events else 0

    @property
    def event_count(self) -> int:
        return len(self._events)

    def last_event_at(self, t_ms: int, kind: str):
        """The last recorded event of ``kind`` at or before ``t_ms`` (or None)."""
        evs = self._by_kind.get(kind)
        if not evs:
            return None
        times = [e.get("t_ms", 0) for e in evs]
        i = bisect.bisect_right(times, t_ms) - 1
        return evs[i] if i >= 0 else None

    def first_time_reaching(self, system: str, index: int):
        """Timeline ms when ``system`` first reached step ``index`` (or None).

        Scans the recorded progress snapshots in order for the first where the
        subsystem's current-step index is at or past ``index`` — i.e. the
        moment that step became active. Lets the GUI seek to a *protocol
        position* (click a step / pick a round) rather than a raw time.
        """
        for ev in self._by_kind.get("progress", []):
            pair = (ev.get("payload") or {}).get(system)
            if isinstance(pair, (list, tuple)) and pair and pair[0] >= index:
                return ev.get("t_ms", 0)
        return None

    def state_at(self, t_ms: int) -> dict:
        """Map each kind to its last event at/<= ``t_ms`` (for inspection/tests)."""
        out = {}
        for kind, evs in self._by_kind.items():
            ev = self.last_event_at(t_ms, kind)
            if ev is not None:
                out[kind] = ev
        return out

    def seek(self, t_ms: int) -> None:
        """Reconstruct and push every stream's state as of ``t_ms``.

        For each kind, the LAST event at or before ``t_ms`` is dispatched to
        its sink, so the live panels show the frame/metrics that were current
        then, the schematic the valve state, and the step list the active step.
        """
        for kind in _LIVE_KINDS:
            ev = self.last_event_at(t_ms, kind)
            if ev is not None and self._emit_update is not None:
                self._emit_update(
                    LiveUpdate(
                        kind=kind,
                        run_id=ev.get("run_id"),
                        payload=decode_payload(ev.get("payload") or {}),
                    )
                )
        prog = self.last_event_at(t_ms, "progress")
        if prog is not None and self._set_progress is not None:
            self._set_progress(self._as_progress(prog.get("payload") or {}))
        fluid = self.last_event_at(t_ms, "fluid_state")
        if fluid is not None and self._set_fluid_state is not None:
            self._set_fluid_state(fluid.get("payload") or {})
        # Webcam clips are resolved by the sink from the scrub time directly.
        if self._set_webcam is not None:
            self._set_webcam(t_ms)

    @staticmethod
    def _as_progress(payload: dict) -> dict:
        """Coerce a recorded progress payload back to ``{sys: (cur, total)}``.

        JSON turns the ``(cur, total)`` tuples into 2-lists; restore tuples so
        the consumer (``experiment_tab`` shading) sees the same shape the live
        ``progress()`` returns.
        """
        out = {}
        for system, pair in (payload or {}).items():
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out[system] = (pair[0], pair[1])
        return out
