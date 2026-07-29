"""GUI entry point.

Builds the shared service objects, makes monet share PycroFlow's
Micro-Manager Core (so the embedded monet tab and PycroFlow imaging use one
connection in one process), and runs the Qt event loop.
"""

import sys

import PycroFlow


def _require_pyqt6():
    """Import PyQt6, or exit with an actionable message."""
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "PyQt6 is required for the PycroFlow GUI but is not installed.\n"
            'Install it with:  pip install -e ".[gui]"\n'
        )
        raise SystemExit(2)


def build_main_window():
    """Construct services + the main window (no event loop). Separated from
    :func:`main` so tests can build the window headless."""
    from PycroFlow.services import ExperimentService, SystemService, mm_core
    from PycroFlow.gui.main_window import PycroFlowMainWindow

    # Share PycroFlow's MM Core with the embedded monet GUI so both use one
    # connection in this single process (ADR 006). No-op if monet absent.
    mm_core.share_with_monet()

    experiment_service = ExperimentService()
    system_service = SystemService()
    window = PycroFlowMainWindow(experiment_service, system_service)
    return window


def main(argv=None):
    _require_pyqt6()
    from PyQt6.QtWidgets import QApplication

    PycroFlow.setup_logging(clean_old=True)

    app = QApplication(argv if argv is not None else sys.argv)
    window = build_main_window()
    window.show()
    # PyQt6 renamed QApplication.exec_() to exec() (exec_ is gone).
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
