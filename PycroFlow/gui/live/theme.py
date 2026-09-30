"""The V0.8 LiveLocalization look & feel, ported verbatim (C31).

The lab's in-production LiveLocalization V0.8 GUI is the ratified frontend
reference (C31): a modern dark theme built from exactly two ingredients —
``QApplication.setStyle("Fusion")`` (the modern Qt base, replacing the platform
"old-school" style) plus one global stylesheet (charcoal ``#17181c`` base, gold
accent ``#d4af5a``/``#f9e2af``, rounded cards, gold-underlined tabs, gradient
progress bars, slim scrollbars). This module carries that stylesheet as
:data:`LIVE_QSS` and applies both via :func:`apply_live_theme`, so the WP-GUI
composable shell renders identically to V0.8.

Kept as data (not inlined) so a single edit re-themes every surface, and so the
shell can carry its own theme when embedded in a host that hasn't themed itself.
The QSS is copied verbatim from ``LiveLocalizationGUI_V0.8.py`` (the MainWindow
global stylesheet); accents match V0.8's ``#d4af5a`` gold.
"""

from __future__ import annotations

# The V0.8 MainWindow global stylesheet, verbatim. Cascades to every child
# widget it is set on (or app-wide), so setting it once themes the whole tree.
LIVE_QSS = """
    QMainWindow, QDialog { background-color: #17181c; }
    QWidget { background-color: #17181c; color: #d6d8de;
              font-size: 12px; }

    /* Cards */
    QGroupBox { background-color: #1d1f24; border: 1px solid #2c2f37;
                border-radius: 9px; margin-top: 12px; padding: 8px 8px 6px 8px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px;
                       color: #d4af5a; font-weight: bold; }

    /* Buttons */
    QPushButton { background-color: #262930; color: #e6e8ee;
                  border: 1px solid #363a44; border-radius: 7px;
                  padding: 6px 13px; }
    QPushButton:hover    { background-color: #2f333c; border-color: #4a4f5c; }
    QPushButton:pressed  { background-color: #202329; }
    QPushButton:disabled { color: #5a5e68; background-color: #1c1e23;
                           border-color: #2a2d34; }
    QPushButton:checked  { background-color: #3a2f10; border-color: #d4af5a;
                           color: #f9e2af; }

    /* Inputs — gold focus glow */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
        background-color: #202329; color: #e6e8ee;
        border: 1px solid #363a44; border-radius: 6px; padding: 3px 6px;
        selection-background-color: #d4af5a; selection-color: #17181c; }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid #d4af5a; }
    QComboBox::drop-down { border: 0; width: 18px; }
    QComboBox QAbstractItemView {
        background-color: #202329; color: #e6e8ee;
        border: 1px solid #363a44; selection-background-color: #3a2f10;
        selection-color: #f9e2af; outline: 0; }

    /* Tabs — bordered boxes so each tab is clearly separated */
    QTabWidget::pane { border: 1px solid #2c2f37; border-radius: 8px;
                       top: -1px; background: #1b1d22; }
    QTabBar::tab { background: #202329; color: #9aa0ac;
                   padding: 6px 14px; margin-right: 3px;
                   border: 1px solid #363a44;
                   border-top-left-radius: 7px;
                   border-top-right-radius: 7px; }
    QTabBar::tab:hover    { background: #262a31; color: #d6d8de;
                            border-color: #4a4f5c; }
    QTabBar::tab:selected { background: #2b2f37; color: #f9e2af;
                            border: 1px solid #d4af5a;
                            border-bottom: 2px solid #d4af5a; }
    QTabBar::tab:!selected { margin-top: 2px; }

    /* Progress bar — gold gradient chunk */
    QProgressBar { background-color: #202329; border: 1px solid #2c2f37;
                   border-radius: 7px; text-align: center; color: #e6e8ee;
                   height: 16px; }
    QProgressBar::chunk {
        border-radius: 6px;
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #a8842f, stop:1 #f9e2af); }

    /* Checkboxes */
    QCheckBox { color: #d6d8de; spacing: 6px; }
    QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px;
                           border: 1px solid #4a4f5c; background: #202329; }
    QCheckBox::indicator:checked { background: #d4af5a; border-color: #d4af5a; }

    /* Slim scrollbars */
    QScrollBar:vertical { background: #17181c; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: #363a44; border-radius: 5px;
                                  min-height: 24px; }
    QScrollBar::handle:vertical:hover { background: #4a4f5c; }
    QScrollBar:horizontal { background: #17181c; height: 10px; margin: 0; }
    QScrollBar::handle:horizontal { background: #363a44; border-radius: 5px;
                                    min-width: 24px; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

    /* Inert (planned, not-yet-wired) controls — see PycroFlow.gui.live.inert.
       Dimmed + dashed + italic so a tester reads them as "not implemented yet",
       never as broken. The tooltip names what will wire them. */
    *[inert="true"] { color: #74777e; border: 1px dashed #4a4f5c;
                      font-style: italic; }
    QPushButton[inert="true"] { background-color: #1c1e23; }
    QPushButton[inert="true"]:hover { background-color: #1c1e23;
                                      border: 1px dashed #6b6f78; }

    /* Misc */
    QLabel { color: #d6d8de; background: transparent; }
    QStatusBar { color: #9aa0ac; background: #14151a; }
    QToolTip { background-color: #14151a; color: #e6e8ee;
               border: 1px solid #d4af5a; border-radius: 5px; padding: 4px 6px; }
    QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
"""

# The V0.8 accent palette, exposed so per-widget code can match the theme
# instead of hard-coding its own colours.
ACCENT_GOLD = "#d4af5a"
ACCENT_GOLD_BRIGHT = "#f9e2af"
BG_BASE = "#17181c"
FG_TEXT = "#d6d8de"


def apply_live_theme(app) -> None:
    """Apply the V0.8 look to a ``QApplication``: Fusion base + :data:`LIVE_QSS`.

    Fusion is the load-bearing half — it replaces the platform's default
    ("old-school") style with the modern flat base the stylesheet builds on;
    without it the QSS renders on top of native tabs/buttons and still looks
    dated. Setting the QSS app-wide themes the window chrome too (the shell's
    own copy only reaches its subtree). Safe to call once at startup.
    """
    try:
        app.setStyle("Fusion")
    except Exception:  # noqa: BLE001 - theming must never break startup
        pass
    app.setStyleSheet(LIVE_QSS)
