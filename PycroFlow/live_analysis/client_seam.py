"""The thin CLIENT boundary the LiveAnalysisService pushes state over.

Designed as a seam NOW so a WebSocket/SSE + REST client is a later drop-in
without touching the service: the service owns acquisition + the pipeline (a
headless SERVER) and speaks to any number of clients only through this narrow
interface — a one-way **update channel** (metrics / thumbnails / state) plus
inbound **control calls** (early-abort). Today the only transport is in-process
(direct callbacks; the Qt GUI adapts them onto the GUI thread via qt_bridge).
Tomorrow a ``WebSocketLiveClient`` implements the same two methods over the wire.

Keeping the service <-> client contract this small is the whole point: the
Quality tab is just the first client over it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LiveUpdate:
    """One server->client push. ``kind`` selects the payload's meaning.

    kinds: ``"state"`` (lifecycle transition), ``"metrics"`` (a RunningMetrics
    snapshot + lag stats), ``"thumbnail"`` (a small preview image / render),
    ``"log"`` (a human line), ``"record"`` (the per-FOV registry record ids).
    """

    kind: str
    run_id: str | None = None
    payload: dict = field(default_factory=dict)


class LiveAnalysisClient(abc.ABC):
    """A consumer of the service's update channel + issuer of control calls.

    The service calls :meth:`on_update` (never blocking, best-effort — a slow or
    failing client must not stall acquisition). A client requests early-abort by
    calling the service's own ``request_abort`` (not defined here — the control
    direction is a plain method call the client holds a reference to), so this
    ABC only needs the receive side to be pluggable.
    """

    @abc.abstractmethod
    def on_update(self, update: LiveUpdate) -> None: ...


class CallbackClient(LiveAnalysisClient):
    """In-process client that forwards updates to a plain callable.

    The default/only transport today. The Qt Quality tab wraps its Qt-signal
    emit in such a callback; a headless script can pass ``print``-like sinks.
    """

    def __init__(self, callback: Callable[[LiveUpdate], None]):
        self._cb = callback

    def on_update(self, update: LiveUpdate) -> None:
        self._cb(update)


class UpdateHub:
    """Fan-out of :class:`LiveUpdate`\\ s to registered clients, fail-isolated.

    The service pushes here; every registered client's :meth:`on_update` is
    called, and one client raising is logged and swallowed so it can't affect
    acquisition or the other clients.
    """

    def __init__(self):
        self._clients: list[LiveAnalysisClient] = []

    def add(self, client: LiveAnalysisClient) -> None:
        self._clients.append(client)

    def remove(self, client: LiveAnalysisClient) -> None:
        if client in self._clients:
            self._clients.remove(client)

    def push(self, update: LiveUpdate) -> None:
        from loguru import logger

        for client in list(self._clients):
            try:
                client.on_update(update)
            except Exception as exc:  # noqa: BLE001 - a bad client is isolated
                logger.warning(
                    "live client raised on update: {!r}".format(exc)
                )

    def push_kind(
        self, kind: str, run_id: str | None = None, **payload: Any
    ) -> None:
        self.push(LiveUpdate(kind=kind, run_id=run_id, payload=payload))
