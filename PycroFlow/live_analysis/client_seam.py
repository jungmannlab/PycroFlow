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

    The LAST ``state`` update is sticky: a client registering late (the Live
    tab connects at ORCHESTRATING, after the service already pushed
    ``experiment_started``; a second GUI attaching mid-run) receives it on
    :meth:`add`, so a freshly attached view shows the run's actual state
    instead of sitting on "idle" through a long fluid phase.
    """

    def __init__(self):
        self._clients: list[LiveAnalysisClient] = []
        self._last_state: LiveUpdate | None = None

    def add(self, client: LiveAnalysisClient) -> None:
        self._clients.append(client)
        if self._last_state is not None:
            from loguru import logger

            try:
                client.on_update(self._last_state)
            except Exception as exc:  # noqa: BLE001 - same isolation as push
                logger.warning(
                    "live client raised on sticky state: {!r}".format(exc)
                )

    def remove(self, client: LiveAnalysisClient) -> None:
        if client in self._clients:
            self._clients.remove(client)

    def push(self, update: LiveUpdate) -> None:
        from loguru import logger

        if update.kind == "state":
            self._last_state = update
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


def push_thumbnail(
    hub,
    run_id,
    frame,
    *,
    pixelsize_nm=None,
    boxes=None,
    box_size=None,
) -> None:
    """Push one Overview-renderable ``thumbnail`` update. Never raises.

    The single construction of the thumbnail payload (raw bytes + shape +
    dtype + scale, optional picasso-style detection ``boxes``) shared by
    every pusher — the demo/live launchers and the MM-preview session — so
    the schema the Overview consumes cannot drift between them.

    Parameters
    ----------
    hub : UpdateHub
        The service's update hub.
    run_id : str or None
        The run the update is tagged with.
    frame : numpy.ndarray
        The 2-D frame to show (sent as raw bytes).
    pixelsize_nm : float or None
        Sample-plane pixel size for the scale bar — pass the EFFECTIVE value
        (multiply by the stride when sending a downsampled frame).
    boxes, box_size : list or None, int or None
        Optional detection overlay (flat [x0, y0, x1, y1, ...] + box size).
    """
    from loguru import logger

    try:
        payload = {
            "data": frame.tobytes(),
            "shape": frame.shape,
            "dtype": str(frame.dtype),
            "pixelsize_nm": pixelsize_nm,
        }
        if boxes is not None:
            payload["boxes"] = boxes
            payload["box_size"] = box_size
        hub.push_kind("thumbnail", run_id, **payload)
    except Exception as exc:  # noqa: BLE001 - a thumbnail must never hurt
        logger.warning("thumbnail push failed: {!r}".format(exc))
