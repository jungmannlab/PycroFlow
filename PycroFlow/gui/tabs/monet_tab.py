"""Monet tab: embeds monet's own GUI in-process.

monet's ``MonetMainWindow`` is a ``QMainWindow`` (itself a ``QWidget``), so
it can be dropped into this tab's layout and rendered without its own
top-level window. Running it in the same process as PycroFlow means both
share one Micro-Manager Core (see
:func:`PycroFlow.services.mm_core.share_with_monet` and ADR 006),
eliminating the two-process MM connection conflict.

monet is an external sibling dependency (ADR 004); when it isn't installed
the tab degrades to an explanatory placeholder instead of crashing the GUI.

Binding: monet must be on the same Qt binding as PycroFlow's GUI (PyQt6) to
embed. :meth:`_make_monet_window` checks ``MonetMainWindow`` is a PyQt6
``QWidget`` subclass *before* constructing it, falling back to the
placeholder for a mocked monet (tests) or a monet built against a different
binding (e.g. PyQt5) — constructing the latter would emit "Must construct a
QApplication before a QWidget" and crash the process.
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
                "Install the monet sibling package to enable "
                "laser/illumination\n"
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
        # Verify monet's window is a PyQt6 QWidget *subclass* before
        # constructing it. Two cases this guards against:
        #   * monet is mocked (tests) — MonetMainWindow is not a class;
        #   * monet was built against a different Qt binding (e.g. PyQt5),
        #     so its window is not a PyQt6 QWidget. Instantiating that emits
        #     "Must construct a QApplication before a QWidget" and crashes
        #     the process before the try/except below can catch it.
        # In both cases fall back to the placeholder instead of crashing.
        if not (isinstance(MonetMainWindow, type)
                and issubclass(MonetMainWindow, QWidget)):
            return None, (
                "monet.gui.MonetMainWindow is not a PyQt6 QWidget — monet is "
                "mocked, or built against a different Qt binding than "
                "PycroFlow's PyQt6 GUI (it cannot be embedded then)."
            )
        try:
            window = MonetMainWindow()
        except Exception as exc:
            return None, "MonetMainWindow construction failed: {!r}".format(
                exc)
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
