"""Headless tests for the Qt setup wizard (offscreen Qt, no instruments).

Drives the wizard pages' widgets directly and calls ``commit()`` — the single
persistence seam — so the write path is exercised without an event loop, plus a
light main-window integration (the toolbar action refreshes the setup combo).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt6  # noqa: F401

    _HAVE_PYQT6 = True
except Exception:  # pragma: no cover - base env without the [gui] extra
    _HAVE_PYQT6 = False

from PycroFlow import configs, setup_core  # noqa: E402


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestSetupWizardCommit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pf-wiz-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        env = {
            "PYCROFLOW_HOME": self._tmp,
            "PYCROFLOW_SETUPS_DIR": os.path.join(self._tmp, "setups"),
        }
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def _wizard(self):
        from PycroFlow.gui.setup_wizard import SetupWizard

        wiz = SetupWizard()
        self.addCleanup(wiz.deleteLater)
        return wiz

    def test_commit_writes_env_and_cloned_setup(self):
        wiz = self._wizard()
        wiz.page_conn.registry_url.setText("http://reg:8000")
        wiz.page_conn.registry_token.setText("tok")
        wiz.page_setup.create.setChecked(True)
        i = wiz.page_setup.template.findText("Template")
        wiz.page_setup.template.setCurrentIndex(i)
        wiz.page_setup.name.setText("MyScope")
        wiz.page_setup.emulated.setChecked(False)
        wiz.page_setup.com.setCurrentText("COM7")  # -> normalized to '7'
        wiz.page_setup.monet_config.setText("MyScopeMonet")
        wiz.page_conn.monet_token.setText("mtok")

        summary = wiz.commit()

        self.assertIn("MyScope", summary)
        self.assertEqual(wiz.created_setup, "MyScope")
        env = setup_core.read_env()
        self.assertEqual(env["PAINT_REGISTRY_URL"], "http://reg:8000")
        self.assertEqual(env["PAINT_REGISTRY_TOKEN"], "tok")
        self.assertEqual(env["PAINT_MONET_TOKEN"], "mtok")
        # The created setup is recorded as the default microscope (.env).
        self.assertEqual(env["PYCROFLOW_SETUP"], "MyScope")
        self.assertIn("MyScope", configs.list_setups())
        loaded = configs.load_setup("MyScope")
        self.assertEqual(loaded["setup"], "MyScope")
        # Hamilton wants the bare number, not "COM7".
        self.assertEqual(loaded["fluid"]["pumps"]["interface"]["COM"], "7")
        self.assertEqual(loaded["illumination"]["config"], "MyScopeMonet")

    def test_commit_connectivity_only(self):
        wiz = self._wizard()
        wiz.page_setup.create.setChecked(False)
        wiz.page_conn.spill_port.setText("COM5")
        wiz.commit()
        self.assertIsNone(wiz.created_setup)
        self.assertEqual(setup_core.read_env()["PYCROFLOW_SPILL_PORT"], "COM5")
        # Nothing saved to the (isolated, empty) user setups dir.
        self.assertFalse(list(configs.user_setups_dir().glob("*.yaml")))

    def test_validatepage_rejects_blank_and_duplicate_names(self):
        from PyQt6.QtWidgets import QMessageBox

        wiz = self._wizard()
        wiz.page_setup.create.setChecked(True)
        with mock.patch.object(QMessageBox, "warning") as warn:
            wiz.page_setup.name.setText("")  # blank
            self.assertFalse(wiz.page_setup.validatePage())
            wiz.page_setup.name.setText("Emulator")  # collides with built-in
            self.assertFalse(wiz.page_setup.validatePage())
        self.assertGreaterEqual(warn.call_count, 2)

    def test_commit_reports_save_failure_without_raising(self):
        # commit() runs from the summary page's initializePage — a persistence
        # failure must be reported in the summary, not raised into the Qt UI.
        from PycroFlow import setup_core

        wiz = self._wizard()
        wiz.page_setup.create.setChecked(True)
        wiz.page_setup.name.setText("FailRig")
        wiz.page_setup.emulated.setChecked(True)
        with mock.patch.object(
            setup_core, "save_setup", side_effect=OSError("disk full")
        ):
            summary = wiz.commit()  # must not raise
        self.assertIn("Could not save the setup", summary)
        self.assertIn("disk full", summary)
        self.assertIsNone(wiz.created_setup)

    def test_emulated_setup_needs_no_com(self):
        wiz = self._wizard()
        wiz.page_setup.create.setChecked(True)
        wiz.page_setup.name.setText("EmuRig")
        wiz.page_setup.emulated.setChecked(True)
        self.assertTrue(wiz.page_setup.validatePage())
        wiz.commit()
        self.assertTrue(configs.load_setup("EmuRig")["emulated"])


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMainWindowWizardWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Isolate the user setups dir so the fake wizard doesn't write into the
        # committed fixtures; PYCROFLOW_HOME keeps .env out of the checkout too.
        self._tmp = tempfile.mkdtemp(prefix="pf-wizwin-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        p = mock.patch.dict(
            os.environ,
            {
                "PYCROFLOW_HOME": self._tmp,
                "PYCROFLOW_SETUPS_DIR": os.path.join(self._tmp, "setups"),
            },
        )
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        from PycroFlow.tests import chdir_to_test_output

        chdir_to_test_output()

    def _window(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow

        return PycroFlowMainWindow(ExperimentService(), SystemService())

    def test_action_present_and_creates_setup_refreshes_combo(self):
        w = self._window()
        self.addCleanup(w.deleteLater)
        self.assertTrue(hasattr(w, "act_setup_wizard"))
        # Simulate the wizard creating "NewRig": patch the launcher to both
        # save a setup and return its name, then trigger the toolbar action.
        import PycroFlow.gui.setup_wizard as sw

        def _fake_launch(parent=None):
            configs.save_setup("NewRig", {"setup": "NewRig", "emulated": True})
            return "NewRig"

        with mock.patch.object(sw, "run_setup_wizard", _fake_launch):
            w._run_setup_wizard()
        self.assertEqual(w.setup_combo.currentText(), "NewRig")

    def test_pycroflow_setup_env_preselects_the_microscope(self):
        # PYCROFLOW_SETUP picks the default rig on launch (not the first combo
        # entry). "IbidiEmulator" is a shipped setup that sorts after "Emulator".
        with mock.patch.dict(os.environ, {"PYCROFLOW_SETUP": "IbidiEmulator"}):
            w = self._window()
        self.addCleanup(w.deleteLater)
        self.assertEqual(w.setup_combo.currentText(), "IbidiEmulator")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
