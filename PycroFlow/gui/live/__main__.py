"""Run the composable operator frontend standalone: ``python -m PycroFlow.gui.live``.

Builds a :class:`~PycroFlow.gui.live.shell.LiveShell` in its own top-level window
and runs the Qt loop. With no ``--service`` wiring it comes up as a passive shell
(all panels present, no stream) so the layout can be inspected without an
instrument; a real deployment constructs the shell with a live
:class:`~PycroFlow.live_analysis.service.LiveAnalysisService` and attaches it via
:meth:`LiveShell.connect_service` (or passes it to :func:`build_live_shell`).

Kept separate from :mod:`PycroFlow.gui.app` (the acquisition main window) because
the operator frontend depends only on WP-4's *seam*, never on the acquisition
backend — so it can run as its own client, including on a monitoring machine.
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    try:
        from PyQt6.QtWidgets import QApplication, QMainWindow
    except ImportError:
        sys.stderr.write(
            "PyQt6 is required for the PycroFlow live GUI but is not installed.\n"
            'Install it with:  pip install -e ".[gui]"\n'
        )
        return 2

    from PycroFlow.gui.live.shell import build_live_shell
    from PycroFlow.gui.live.theme import apply_live_theme

    app = QApplication(argv if argv is not None else sys.argv)
    apply_live_theme(app)  # V0.8 look: Fusion base + dark/gold stylesheet.
    shell = build_live_shell()  # passive (no service) — layout inspection.
    win = QMainWindow()
    win.setWindowTitle("PycroFlow — Live QC (operator frontend)")
    win.setCentralWidget(shell)
    win.resize(1200, 800)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
