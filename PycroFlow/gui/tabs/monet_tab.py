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
        # PycroFlow's own illumination-system connection status (distinct from
        # monet's embedded laser GUI below).
        self._illu_status = QLabel()
        self._layout.addWidget(self._illu_status)
        # There are two independent connections to the same lasers on this
        # tab, which is easy to misread — spell out which is which.
        self._explainer = QLabel(
            "Two separate connections to the same lasers live here:\n"
            "  • PycroFlow illumination (status above) — used by an "
            "experiment run. It only checks the monet config on Connect; "
            "the lasers open on first use.\n"
            "  • monet's own Connect (below) — manual control between runs. "
            "It opens the lasers immediately.\n"
            "Only one may hold the laser ports at a time, so monet's "
            "controls are disabled while an experiment runs.")
        self._explainer.setWordWrap(True)
        self._explainer.setStyleSheet("color: gray;")
        self._layout.addWidget(self._explainer)
        self.set_illumination_status("not connected")
        self._embed_container = QWidget()
        self._embed_layout = QVBoxLayout(self._embed_container)
        self._embed_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._embed_container, 1)
        self._placeholder = None
        self._embed(None)

    def set_setup(self, setup_name):
        """(Re)embed monet for the given setup (a ``monet.CONFIGS`` key).

        Called when a microscope setup is loaded, so the Monet tab connects to
        the matching monet config.
        """
        self._setup_name = setup_name
        self._embed(setup_name)

    def set_illumination_status(self, text):
        """Show the PycroFlow illumination-system connection status."""
        scope = (" (monet config: {})".format(self._setup_name)
                 if self._setup_name else "")
        self._illu_status.setText(
            "PycroFlow illumination: {}{}".format(text, scope))

    def set_run_lock(self, locked):
        """Disable the embedded monet GUI during an experiment run.

        PycroFlow's IlluminationSystem owns the lasers while running, so the
        manual monet controls (which would grab the same COM port) are greyed
        out to make it clear they are off-limits. The real status is restored
        by the coordinator on unlock.
        """
        self._embed_container.setEnabled(not locked)
        if locked:
            self.set_illumination_status("in use by experiment")

    def _embed(self, setup_name):
        # Tear down whatever is currently embedded.
        self.shutdown()
        while self._embed_layout.count():
            item = self._embed_layout.takeAt(0)
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
                "Detail: {}".format(problem)
            )
            self._embed_layout.addWidget(self._placeholder)
            return
        # Embed monet's widget as a child. It does not own the QApplication
        # loop, so this is safe.
        self._monet_window = window
        self._embed_layout.addWidget(self._monet_window)

    @staticmethod
    def _make_monet_window(setup_name=None):
        """Return (widget, None) on success or (None, reason) on failure.

        monet's own guidance is that embedders use ``MonetWidget`` (a
        ``QWidget``). We fall back to ``MonetMainWindow`` for older monet
        versions.

        The embed is constructed **without auto-connecting** the lasers:
        passing ``initial_microscope`` makes ``MonetWidget`` open the lasers,
        which would fight PycroFlow's own ``IlluminationSystem`` for the same
        COM port (→ "No lasers could be loaded"). PycroFlow owns the lasers
        during automated runs; the Monet tab is a manual tool — the user picks
        the scope (pre-selected here for convenience) and clicks monet's own
        Connect when they want direct laser control (not while running).

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
        cls = getattr(mg, "MonetWidget", None) or getattr(
            mg, "MonetMainWindow", None
        )
        if cls is None:
            return None, "monet.gui has no MonetWidget / MonetMainWindow"
        if not (isinstance(cls, type) and issubclass(cls, QWidget)):
            return None, (
                "monet's embed widget is not a PyQt6 QWidget — monet is "
                "mocked, or built against a different Qt binding than "
                "PycroFlow's PyQt6 GUI (it cannot be embedded then)."
            )
        try:
            # NB: no initial_microscope -> no laser auto-connect.
            window = cls()
        except Exception as exc:
            return None, "construction failed: {!r}".format(exc)
        # Pre-select the scope in monet's own combo for convenience (display
        # only — does not connect). Best-effort; private API may be absent.
        if setup_name:
            combo = getattr(window, "_scope_combo", None)
            if combo is not None:
                try:
                    idx = combo.findText(setup_name)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                except Exception:
                    pass
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
