"""Per-orchestrator thread-coordination state.

Wraps the 12-field dict that previously sat on ``ProtocolOrchestrator`` as
a *class* attribute (``orchestration.core:379-395``). Class-level state
meant two ``ProtocolOrchestrator`` instances in the same process shared
locks, events, and the per-subsystem message lists, producing crosstalk
the moment anyone spun up two protocols (e.g. for a pause/abort safety
test or — in Stage 5 — a GUI that ran a smoke protocol while the user
was preparing the real one).

:class:`ThreadExchange` solves that by:

* being a per-instance object built by :func:`ThreadExchange.create`,
* exposing typed attributes for known fields (``fluid_lock``,
  ``signal_registry``, ...) so editor autocomplete works,
* still implementing ``__getitem__`` / ``__setitem__`` / ``__contains__``
  so every existing ``self.txchange['fluid']`` call site keeps working
  unchanged.

The Stage-4 signal registry lives here as an attribute. The handler
fires/waits through ``tx.signal_registry``; the legacy list-of-strings
path is preserved in the per-subsystem ``list[str]`` for substring-
matching semantics that callers still depend on.
"""

from __future__ import annotations

import queue
import threading
from typing import List

from PycroFlow.orchestration.signal_registry import SignalRegistry

_SUBSYSTEMS = ("fluid", "img", "illu")


class ThreadExchange(dict):
    """Dict-shaped container of thread-coordination primitives.

    Instances are mutable per-orchestrator. Pre-Stage-4 callers that do
    ``txchange['fluid_lock']`` see the same behaviour as before; new code
    can use the typed attributes — e.g. ``txchange.signal_registry`` or
    ``txchange.fluid_lock``.

    Do NOT use ``ThreadExchange()`` directly — use
    :meth:`ThreadExchange.create` so each instance gets fresh primitives.
    Constructing from a dict literal would alias the locks across
    instances.
    """

    @classmethod
    def create(cls) -> "ThreadExchange":
        """Build a fresh exchange with all primitives newly allocated."""
        tx = cls()
        for sub in _SUBSYSTEMS:
            tx[f"{sub}_lock"] = threading.Lock()
            tx[sub] = []  # list[str]: legacy message log
            tx[f"{sub}_finished"] = threading.Event()
        tx["fluid_queue"] = queue.Queue()
        tx["start_protocol_flag"] = threading.Event()
        tx["pause_protocol_flag"] = threading.Event()
        tx["abort_protocol_flag"] = threading.Event()
        tx["abort_flag"] = threading.Event()
        tx["graceful_stop_flag"] = threading.Event()
        tx["signal_registry"] = SignalRegistry()
        return tx

    # --- Typed accessors. Read-only — modifying through the typed name
    # would diverge from the dict storage that legacy code still reads.

    @property
    def signal_registry(self) -> SignalRegistry:
        return self["signal_registry"]

    @property
    def fluid_lock(self) -> threading.Lock:
        return self["fluid_lock"]

    @property
    def img_lock(self) -> threading.Lock:
        return self["img_lock"]

    @property
    def illu_lock(self) -> threading.Lock:
        return self["illu_lock"]

    @property
    def fluid(self) -> List[str]:
        return self["fluid"]

    @property
    def img(self) -> List[str]:
        return self["img"]

    @property
    def illu(self) -> List[str]:
        return self["illu"]

    @property
    def abort_flag(self) -> threading.Event:
        return self["abort_flag"]

    @property
    def abort_protocol_flag(self) -> threading.Event:
        return self["abort_protocol_flag"]

    @property
    def pause_protocol_flag(self) -> threading.Event:
        return self["pause_protocol_flag"]

    @property
    def start_protocol_flag(self) -> threading.Event:
        return self["start_protocol_flag"]

    @property
    def graceful_stop_flag(self) -> threading.Event:
        return self["graceful_stop_flag"]

    @property
    def fluid_finished(self) -> threading.Event:
        return self["fluid_finished"]

    @property
    def img_finished(self) -> threading.Event:
        return self["img_finished"]

    @property
    def illu_finished(self) -> threading.Event:
        return self["illu_finished"]

    @property
    def fluid_queue(self) -> queue.Queue:
        return self["fluid_queue"]
