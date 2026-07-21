"""Run blocking calls off the GUI thread so the UI stays responsive.

``run_in_background(owner, fn, on_done=, on_error=)`` runs ``fn()`` in a
``QThread`` and invokes ``on_done(result)`` / ``on_error(exc)`` back on the GUI
thread (the callbacks are slots on a GUI-thread ``QObject``, so cross-thread
signals are delivered queued). The task keeps itself alive on
``owner._bg_tasks`` until it finishes.

Tests can call :func:`set_synchronous(True)` to run ``fn`` inline (no thread),
so assertions about the call and the callbacks hold deterministically.
"""

from PyQt6.QtCore import QObject, QThread, pyqtSignal

_SYNCHRONOUS = False


def set_synchronous(flag):
    """Run background tasks inline (no thread). For tests."""
    global _SYNCHRONOUS
    _SYNCHRONOUS = bool(flag)


class _Worker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as exc:  # surfaced via on_error on the GUI thread
            self.failed.emit(exc)
        else:
            self.finished.emit(result)


class BackgroundTask(QObject):
    """A single fn() run on its own QThread, with GUI-thread callbacks."""

    def __init__(self, owner, fn, on_done=None, on_error=None):
        super().__init__(owner)  # GUI-thread affinity (owner is a widget)
        self._owner = owner
        self._on_done = on_done
        self._on_error = on_error
        self._thread = QThread(self)
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

    def start(self):
        self._thread.start()

    def _on_finished(self, result):
        self._stop()
        if self._on_done is not None:
            self._on_done(result)
        self._dispose()

    def _on_failed(self, exc):
        self._stop()
        if self._on_error is not None:
            self._on_error(exc)
        self._dispose()

    def _stop(self):
        self._thread.quit()
        self._thread.wait()

    def _dispose(self):
        tasks = getattr(self._owner, "_bg_tasks", None)
        if tasks is not None:
            tasks.discard(self)
        self.deleteLater()


def run_in_background(owner, fn, on_done=None, on_error=None):
    """Run ``fn()`` off the GUI thread (or inline under set_synchronous)."""
    if _SYNCHRONOUS:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
        else:
            if on_done is not None:
                on_done(result)
        return None
    if not hasattr(owner, "_bg_tasks"):
        owner._bg_tasks = set()
    task = BackgroundTask(owner, fn, on_done, on_error)
    owner._bg_tasks.add(task)
    task.start()
    return task
