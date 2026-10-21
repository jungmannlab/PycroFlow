"""Record a run's live streams to a JSONL timeline for post-run replay.

A run's live view (image + detection boxes), QC metrics, fluidics state and
run-sequence progress are all transient today — they flow through the
:class:`~PycroFlow.live_analysis.client_seam.UpdateHub` seam and the GUI poll
timers and are gone once the run ends. :class:`ReplayRecorder` taps those same
streams and appends one JSON object per event to a ``*_replay.jsonl`` file next
to the run's data, so :class:`PycroFlow.gui.live.replay_player.ReplayController`
can scrub the whole GUI afterwards.

The recorder is best-effort and must never disturb a run: every write is
guarded, and the image bytes already carried by ``thumbnail`` updates are
base64-encoded inline (no extra frame capture). Timestamps are milliseconds
from :meth:`start`, taken from an injectable monotonic clock so the format is
deterministic in tests.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Optional

from PycroFlow.live_analysis.client_seam import LiveUpdate


def encode_payload(payload: dict) -> dict:
    """Return a JSON-safe copy of an update payload.

    ``bytes`` (the raw thumbnail frame) become ``{"__b64__": "..."}``; tuples
    become lists; anything else JSON cannot represent is coerced with ``repr``
    so a write never fails. The transform is shallow-recursive over dicts and
    lists, which covers every payload the seam produces.
    """
    return _jsonable(payload)


def decode_payload(payload: Any) -> Any:
    """Inverse of :func:`encode_payload`: restore ``bytes`` from ``__b64__``."""
    if isinstance(payload, dict):
        if set(payload.keys()) == {"__b64__"}:
            return base64.b64decode(payload["__b64__"])
        return {k: decode_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [decode_payload(v) for v in payload]
    return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # numpy scalars and the like: fall back to a Python number, else repr.
    try:
        return float(value)
    except (TypeError, ValueError):
        return repr(value)


class ReplayRecorder:
    """Append a run's live events to a JSONL timeline. Best-effort, never raises.

    Parameters
    ----------
    path : str
        Destination ``*_replay.jsonl`` file (overwritten on :meth:`start`).
    clock : callable, optional
        Monotonic clock returning seconds (default :func:`time.monotonic`);
        injectable so tests get deterministic timestamps.
    """

    def __init__(self, path: str, clock: Callable[[], float] = time.monotonic):
        self._path = path
        self._clock = clock
        self._fh = None
        self._t0: Optional[float] = None
        self._t0_utc: Optional[float] = None
        self._run_id: Optional[str] = None

    @property
    def path(self) -> str:
        return self._path

    @property
    def active(self) -> bool:
        return self._fh is not None

    @property
    def t0_utc(self) -> Optional[float]:
        """Wall-clock epoch (seconds) of :meth:`start`, for aligning external
        artefacts (webcam ``.avi`` clips carry wall-clock timestamps)."""
        return self._t0_utc

    def start(self, run_id: Optional[str] = None, protocol=None) -> None:
        """Open the timeline file and stamp t0. Idempotent-safe.

        ``protocol`` (the compiled Run Sequence dict) is embedded in the header
        so the timeline file is self-contained: opening it later reconstructs
        the step lists for the step-highlight without a separate YAML.
        """
        self.stop()
        self._run_id = run_id
        try:
            self._fh = open(self._path, "w", encoding="utf-8")
            self._t0 = self._clock()
            # Real wall-clock anchor (NOT the injectable monotonic clock), so
            # the replay can map a webcam clip's UTC timestamp to a timeline ms.
            self._t0_utc = time.time()
            # A header meta line carries the anchor + the run's protocol so a
            # controller loaded from the file alone can align external artefacts
            # and populate the step lists.
            payload = {"t0_utc": self._t0_utc, "run_id": run_id}
            if protocol:
                payload["protocol"] = _jsonable(protocol)
            meta = {"t_ms": 0, "kind": "meta", "payload": payload}
            self._fh.write(json.dumps(meta) + "\n")
            self._fh.flush()
        except Exception:  # noqa: BLE001 - recording must never stop a run
            self._fh = None

    def stop(self) -> None:
        """Flush and close the timeline file (safe to call repeatedly)."""
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass

    # -- event sinks --------------------------------------------------------

    def on_update(self, update: LiveUpdate) -> None:
        """Record a seam :class:`LiveUpdate` (state/metrics/thumbnail/log)."""
        self._write(
            update.kind,
            encode_payload(update.payload or {}),
            run_id=update.run_id,
        )

    def record_progress(self, progress: dict) -> None:
        """Record a run-sequence progress snapshot (``{sys: (cur, total)}``)."""
        self._write("progress", _jsonable(progress or {}))

    def record_fluid_state(self, state: dict) -> None:
        """Record a fluidics-state snapshot (valve/syringe/multiplexer)."""
        self._write("fluid_state", _jsonable(state or {}))

    # -- writing ------------------------------------------------------------

    def _write(self, kind: str, payload: dict, run_id=None) -> None:
        if self._fh is None or self._t0 is None:
            return
        try:
            record = {
                "t_ms": int(round((self._clock() - self._t0) * 1000.0)),
                "kind": kind,
                "payload": payload,
            }
            if run_id is not None:
                record["run_id"] = run_id
            self._fh.write(json.dumps(record) + "\n")
            self._fh.flush()
        except Exception:  # noqa: BLE001 - a bad write must never hurt a run
            pass
