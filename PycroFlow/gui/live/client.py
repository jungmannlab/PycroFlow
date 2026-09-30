"""The thin subscribing CLIENT the operator frontend consumes the stream over.

WP-4 shipped the service<->client seam (:mod:`PycroFlow.live_analysis.client_seam`):
a one-way **update channel** (``on_update(LiveUpdate)``) plus an inbound
**control call** (early-abort). This module is the GUI's side of that seam and
the **single transport-swap point** (A13/C23):

* Today the transport is *in-process*: the service's :class:`UpdateHub` calls
  our :class:`~PycroFlow.live_analysis.client_seam.CallbackClient` directly, on a
  worker thread. :class:`LiveClientBridge` re-emits each update as a Qt signal so
  every widget touch happens on the GUI thread (the ``qt_bridge`` pattern).
* Tomorrow a ``WebSocketLiveClient`` implements the same :class:`LiveClient`
  surface — an :meth:`on_update` sink + a :meth:`request_abort` control call —
  and the panels do not change. That is why the panels bind to :class:`LiveClient`
  (an interface), never to :class:`LiveAnalysisService`.

**Multi-client** is a property of the seam, not of this class: several
:class:`LiveClientBridge` instances (each its own GUI window / process) register
their own :class:`CallbackClient` with the one service ``UpdateHub``; the hub
fans out fail-isolated, so two frontends can watch one run. Each bridge tracks
its own ``run_id`` / trends independently.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from PyQt6.QtCore import QObject, pyqtSignal

from PycroFlow.live_analysis.client_seam import CallbackClient, LiveUpdate


@runtime_checkable
class LiveClient(Protocol):
    """What every panel needs from the stream, independent of transport.

    An update source (subscribe a slot to :attr:`update`) plus the two control
    calls the operator UI issues. The in-process :class:`LiveClientBridge`
    implements it today; a remote client implements the same surface later.
    """

    update: "pyqtSignal"

    def request_abort(self) -> None:
        """Issue the service's early-abort control call (best-effort)."""
        ...

    def close(self) -> None:
        """Detach from the stream (unregister from the hub / drop the socket)."""
        ...


class LiveClientBridge(QObject):
    """In-process :class:`LiveClient`: seam updates -> Qt signal + control call.

    Register :attr:`seam_client` with the service's
    :class:`~PycroFlow.live_analysis.client_seam.UpdateHub` (done for you when a
    ``service`` is passed). The hub calls :meth:`_on_update` from a worker
    thread; the ``update`` signal marshals onto the GUI event loop (default
    ``AutoConnection``) so slots run on the GUI thread.

    Parameters
    ----------
    service : LiveAnalysisService or None
        When given, the bridge subscribes to ``service.hub`` and routes
        :meth:`request_abort` to ``service.request_abort``. None is a passive
        view (construction in tests / a not-yet-connected shell).
    """

    update = pyqtSignal(object)  # LiveUpdate

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._hub = getattr(service, "hub", None)
        self.seam_client = CallbackClient(self._on_update)
        # NOTE: WP-4's UpdateHub.add/remove is UNLOCKED. Safe today because
        # every add/remove/push runs in one process under the GIL; if the
        # transport ever goes off-process (the future remote-client WO), the
        # hub's client list needs a lock.
        if self._hub is not None:
            self._hub.add(self.seam_client)

    # -- update channel (worker thread -> GUI thread) ----------------------
    def _on_update(self, update: LiveUpdate) -> None:
        # Marshalling happens via the queued signal connection; keep this cheap.
        self.update.emit(update)

    # -- control direction --------------------------------------------------
    def request_abort(self) -> None:
        if self._service is not None:
            self._service.request_abort()

    # -- lifecycle ----------------------------------------------------------
    def attach(self, service) -> None:
        """(Re)bind to a service after construction (shell connects lazily)."""
        self.close()
        self._service = service
        self._hub = getattr(service, "hub", None)
        if self._hub is not None:
            self._hub.add(self.seam_client)

    def close(self) -> None:
        if self._hub is not None:
            self._hub.remove(self.seam_client)
        self._hub = None

    @property
    def service(self) -> Optional[object]:
        return self._service
