"""Adapter from ExperimentService observer callbacks to Qt signals.

``ExperimentService`` notifies observers with plain Python callbacks that
may fire on the orchestrator's worker threads. Touching Qt widgets from a
non-GUI thread is undefined behaviour, so this bridge re-emits each
callback as a Qt signal. Because the bridge ``QObject`` lives in the GUI
thread and the signals use the default ``AutoConnection``, Qt automatically
queues cross-thread emissions onto the GUI event loop — slots connected to
these signals run safely on the GUI thread.

Tabs/widgets connect to :class:`QtBridge` signals instead of registering
service observers directly, so no widget ever runs on a worker thread.
"""

from PyQt6.QtCore import QObject, pyqtSignal

from PycroFlow.services.experiment_service import ExperimentState


class QtBridge(QObject):
    """Re-emits ExperimentService observer callbacks as Qt signals.

    Signals:
        state_changed(object, object): (old ExperimentState, new ExperimentState)
        log_message(str): a human-readable log line
    """

    state_changed = pyqtSignal(object, object)
    log_message = pyqtSignal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service
        # Register our emitters as service observers. The service calls
        # these from whatever thread triggered the transition; the signal
        # emission marshals onto the GUI thread.
        service.add_state_observer(self._on_state)
        service.add_log_observer(self._on_log)

    def _on_state(self, old: ExperimentState, new: ExperimentState):
        self.state_changed.emit(old, new)

    def _on_log(self, message: str):
        self.log_message.emit(message)
