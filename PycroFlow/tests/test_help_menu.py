"""Headless GUI tests for the Help menu (About / License / Check for Updates).

Offscreen Qt; the update check runs synchronously (worker.set_synchronous) so
the three outcomes are asserted without an event loop or the network.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt6  # noqa: F401

    _HAVE_PYQT6 = True
except Exception:  # pragma: no cover - base env without the [gui] extra
    _HAVE_PYQT6 = False


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestHelpMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        from PycroFlow.gui.widgets.worker import set_synchronous
        from PycroFlow.tests import chdir_to_test_output

        set_synchronous(False)
        chdir_to_test_output()

    def _window(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow

        w = PycroFlowMainWindow(ExperimentService(), SystemService())
        self.addCleanup(w.deleteLater)
        return w

    def test_help_menu_has_three_actions(self):
        w = self._window()
        help_menu = None
        for act in w.menuBar().actions():
            if "Help" in act.text() and act.menu() is not None:
                help_menu = act.menu()
        self.assertIsNotNone(help_menu)
        labels = [a.text() for a in help_menu.actions() if a.text()]
        self.assertIn("Check for Updates…", labels)
        self.assertIn("License", labels)
        self.assertIn("About PycroFlow", labels)

    def test_license_text_reads_license_md(self):
        w = self._window()
        text = w._license_text()
        self.assertIn("Grabmayr", text)
        self.assertIn("Redistribution", text)  # BSD 2-clause body

    def test_about_dialog_shows_version(self):
        import PycroFlow

        w = self._window()
        shown = {}
        with mock.patch.object(
            w,
            "_message_with_links",
            lambda title, html, icon=None: shown.update(
                title=title, html=html
            ),
        ):
            w._show_about()
        self.assertIn("About", shown["title"])
        self.assertIn(PycroFlow.__version__, shown["html"])
        self.assertIn("jungmannlab/PycroFlow", shown["html"])

    def test_check_updates_up_to_date(self):
        from PyQt6.QtWidgets import QMessageBox
        from PycroFlow.gui.widgets.worker import set_synchronous
        from PycroFlow import updates

        set_synchronous(True)
        w = self._window()
        with (
            mock.patch.object(
                updates,
                "check_for_update",
                return_value={
                    "current": "1.0.0",
                    "latest": "1.0.0",
                    "update_available": False,
                    "url": updates.RELEASES_URL,
                    "error": None,
                },
            ),
            mock.patch.object(QMessageBox, "information") as info,
        ):
            w._check_for_updates()
        info.assert_called_once()
        self.assertIn("up to date", info.call_args.args[2])
        self.assertIsNone(w._update_worker)  # guard cleared

    def test_check_updates_available_and_error(self):
        from PycroFlow.gui.widgets.worker import set_synchronous
        from PycroFlow import updates

        set_synchronous(True)
        w = self._window()
        # (a) update available -> rich-text dialog with both versions.
        msgs = []
        with mock.patch.object(
            w,
            "_message_with_links",
            lambda title, html, icon=None: msgs.append((title, html)),
        ):
            with mock.patch.object(
                updates,
                "check_for_update",
                return_value={
                    "current": "1.0.0",
                    "latest": "2.0.0",
                    "update_available": True,
                    "url": updates.RELEASES_URL,
                    "error": None,
                },
            ):
                w._check_for_updates()
            # (b) network error -> warning dialog mentioning the failure.
            with mock.patch.object(
                updates,
                "check_for_update",
                return_value={
                    "current": "1.0.0",
                    "latest": None,
                    "update_available": False,
                    "url": updates.RELEASES_URL,
                    "error": "offline",
                },
            ):
                w._check_for_updates()
        self.assertIn("Update available", msgs[0][0])
        self.assertIn("2.0.0", msgs[0][1])
        self.assertIn("offline", msgs[1][1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
