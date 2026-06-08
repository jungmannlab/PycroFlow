"""Monet tab: embeds monet's own GUI in-process.

monet's ``MonetMainWindow`` is a ``QMainWindow`` (itself a ``QWidget``), so
it can be dropped into this tab's layout and rendered without its own
top-level window. Running it in the same process as PycroFlow means both
share one Micro-Manager Core (see :func:`PycroFlow.services.mm_core.share_with_monet`
and ADR 006), eliminating the two-process MM connection conflict.

monet is an external sibling dependency (ADR 004); when it isn't installed
the tab degrades to an explanatory placeholder instead of crashing the GUI.

Binding caveat: PycroFlow's GUI is on PyQt6. A PyQt5 build of monet cannot be
embedded — PyQt5 and PyQt6 are distinct Qt libraries and their widgets are not
interchangeable in one process. When monet is still on PyQt5 its
``MonetMainWindow`` is not a PyQt6 ``QWidget``, so the ``isinstance`` guard in
:meth:`_make_monet_window` rejects it and the tab shows the placeholder
(no crash). The embed re-enables automatically once monet ships PyQt6.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


class MonetTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._monet_window = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        window, problem = self._make_monet_window()
        if window is None:
            layout.addWidget(QLabel(
                "monet is not available in this environment.\n\n"
                "Install the monet sibling package to enable laser/illumination\n"
                "control here (pip install -e ../monet).\n\n"
                "Detail: {}".format(problem)))
            return
        # Embed monet's QMainWindow as a child widget. It does not own the
        # QApplication loop, so this is safe.
        self._monet_window = window
        layout.addWidget(self._monet_window)

    @staticmethod
    def _make_monet_window():
        """Return (window, None) on success or (None, reason) on failure.

        Failure modes handled: monet not installed; monet import-time error;
        monet present but mocked (returns a non-QWidget, e.g. under the test
        hardware mocks); monet window construction raising (missing hardware).
        """
        from PyQt6.QtWidgets import QWidget
        try:
            from monet.gui import MonetMainWindow
        except Exception as exc:
            return None, "import failed: {!r}".format(exc)
        try:
            window = MonetMainWindow()
        except Exception as exc:
            return None, "MonetMainWindow construction failed: {!r}".format(exc)
        if not isinstance(window, QWidget):
            # monet is mocked (e.g. tests) — not a real embeddable widget.
            return None, "monet.gui.MonetMainWindow is not a QWidget (mocked?)"
        return window, None

    def shutdown(self):
        """Run monet's own cleanup (disable lasers, cancel workers).

        monet's MonetMainWindow.closeEvent does this; we trigger it
        explicitly when PycroFlow's main window closes, since the embedded
        window never receives its own close event.
        """
        if self._monet_window is None:
            return
        try:
            self._monet_window.close()
        except Exception:
            # Cleanup is best-effort during shutdown.
            pass
