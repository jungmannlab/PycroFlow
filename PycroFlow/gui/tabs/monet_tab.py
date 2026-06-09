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
        self._setup_name = None
        self._layout = QVBoxLayout(self)
        self._placeholder = None
        self._embed(None)

    def set_setup(self, setup_name):
        """(Re)embed monet for the given setup (a ``monet.CONFIGS`` key).

        Called by the System tab when a microscope setup is loaded, so the
        Monet tab connects to the matching monet config.
        """
        self._setup_name = setup_name
        self._embed(setup_name)

    def _embed(self, setup_name):
        # Tear down whatever is currently shown.
        self.shutdown()
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._monet_window = None

        window, problem = self._make_monet_window(setup_name)
        if window is None:
            self._placeholder = QLabel(
                "monet is not available in this environment.\n\n"
                "Install the monet sibling package to enable "
                "laser/illumination\n"
                "control here (pip install -e ../monet).\n\n"
                "Detail: {}".format(problem))
            self._layout.addWidget(self._placeholder)
            return
        # Embed monet's widget as a child. It does not own the QApplication
        # loop, so this is safe.
        self._monet_window = window
        self._layout.addWidget(self._monet_window)

    @staticmethod
    def _make_monet_window(setup_name=None):
        """Return (widget, None) on success or (None, reason) on failure.

        monet's own guidance is that embedders use ``MonetWidget`` (a
        ``QWidget``); ``initial_microscope`` is a ``monet.CONFIGS`` key — i.e.
        the PycroFlow setup name — and the widget auto-connects to it. We fall
        back to ``MonetMainWindow`` for older monet versions.

        Failure modes handled: monet not installed; monet import-time error;
        monet present but mocked (returns a non-QWidget); monet built against
        a different Qt binding (constructing it would crash, so we check the
        class is a PyQt6 ``QWidget`` subclass *before* instantiating).
        """
        from PyQt6.QtWidgets import QWidget
        try:
            import monet.gui as mg
        except Exception as exc:
            return None, "import failed: {!r}".format(exc)
        cls = getattr(mg, 'MonetWidget', None) or getattr(
            mg, 'MonetMainWindow', None)
        if cls is None:
            return None, "monet.gui has no MonetWidget / MonetMainWindow"
        if not (isinstance(cls, type) and issubclass(cls, QWidget)):
            return None, (
                "monet's embed widget is not a PyQt6 QWidget — monet is "
                "mocked, or built against a different Qt binding than "
                "PycroFlow's PyQt6 GUI (it cannot be embedded then)."
            )
        try:
            window = cls(initial_microscope=setup_name)
        except TypeError:
            # Older signature without initial_microscope.
            try:
                window = cls()
            except Exception as exc:
                return None, "construction failed: {!r}".format(exc)
        except Exception as exc:
            return None, "construction failed: {!r}".format(exc)
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
