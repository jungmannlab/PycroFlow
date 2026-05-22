"""PyQt5 GUI frontend for PycroFlow.

Importing this package does NOT import PyQt5 — the Qt-dependent modules
(``app``, ``main_window``, ``qt_bridge``, ``tabs.*``) import it themselves
and are only loaded when the GUI is actually launched. This keeps
``import PycroFlow.gui`` safe in environments without PyQt5 (CI, headless
dev), and lets ``app.main`` emit a clear install hint instead of an
ImportError traceback.

Launch with the ``pycroflow-gui`` console script or
``python -m PycroFlow.gui``.
"""


def main(argv=None):
    """Lazy entry point — defers the PyQt5 import to call time."""
    from PycroFlow.gui.app import main as _main
    return _main(argv)
