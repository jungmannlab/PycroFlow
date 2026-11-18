"""GUI tests for the structured setup editor (tab + wizard dialog).

Headless (``QT_QPA_PLATFORM=offscreen``). The recurring assertion is that the
reservoir wiring the form can't render survives every edit path byte-for-byte.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from PycroFlow import configs


class _QtSetupBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Isolate the user setups dir so Save tests don't touch the fixtures.
        self._tmp = tempfile.mkdtemp(prefix="pf-setuptab-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"PYCROFLOW_SETUPS_DIR": self._tmp})
        p.start()
        self.addCleanup(p.stop)


class TestSetupEditorTab(_QtSetupBase):
    def _tab(self):
        from PycroFlow.gui.tabs.setup_tab import SetupEditorTab

        return SetupEditorTab()

    def test_load_preserves_wiring_through_validate(self):
        raw = configs.load_setup_file("Emulator")
        tab = self._tab()
        tab.load_data(raw, name="Emulator")
        name, merged = tab._validated_result()
        self.assertEqual(name, "Emulator")
        self.assertEqual(
            merged["fluid"]["reservoirs"], raw["fluid"]["reservoirs"]
        )
        self.assertEqual(
            merged["fluid"]["multiplexer"], raw["fluid"]["multiplexer"]
        )

    def test_save_writes_user_setup_and_round_trips(self):
        raw = configs.load_setup_file("IbidiEmulator")  # list-valued valve_pos
        raw["setup"] = "SavedRig"
        saved = []
        from PycroFlow.gui.tabs.setup_tab import SetupEditorTab

        tab = SetupEditorTab(on_saved=lambda n: saved.append(n))
        tab.load_data(raw, name="SavedRig")
        tab._on_save()
        self.assertEqual(saved, ["SavedRig"])
        self.assertIn("SavedRig", configs.list_setups())
        back = configs.load_setup_file("SavedRig")
        # The hard list-valued ibidi wiring survived the save exactly.
        self.assertEqual(
            back["fluid"]["reservoirs"], raw["fluid"]["reservoirs"]
        )
        # The tubing 'from' alias (a Python keyword, Field(alias='from')) is
        # saved under the on-disk key, not the python field name 'from_'.
        self.assertIn("from", back["fluid"]["tubing"][0])
        self.assertNotIn("from_", back["fluid"]["tubing"][0])

    def test_bad_raw_yaml_blocks_current_merged(self):
        tab = self._tab()
        tab.load_data(configs.load_setup_file("Emulator"), name="Emulator")
        tab.raw_edit.setPlainText("this: [is, not: valid: yaml")
        with self.assertRaises(Exception):
            tab.current_merged()

    def test_non_mapping_raw_yaml_rejected(self):
        tab = self._tab()
        tab.load_data(configs.load_setup_file("Emulator"), name="Emulator")
        tab.raw_edit.setPlainText("- just\n- a\n- list")
        with self.assertRaises(ValueError):
            tab.current_merged()

    def test_rename_onto_existing_setup_prompts_before_overwrite(self):
        from PyQt6.QtWidgets import QMessageBox

        tab = self._tab()
        tab.load_setup("Emulator")  # self._name == "Emulator"
        # Rename via the 'setup' field to a DIFFERENT existing setup.
        tab._form.field_editor("setup").line_edit().setText("Template")
        with (
            mock.patch(
                "PycroFlow.gui.tabs.setup_tab.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as q,
            mock.patch(
                "PycroFlow.gui.tabs.setup_tab.configs.save_setup"
            ) as save,
        ):
            tab._on_save()
        q.assert_called_once()
        save.assert_not_called()  # declined -> nothing written

    def test_resave_same_name_does_not_prompt(self):
        tab = self._tab()
        tab.load_setup("Emulator")  # re-saving the loaded setup
        with (
            mock.patch(
                "PycroFlow.gui.tabs.setup_tab.QMessageBox.question"
            ) as q,
            mock.patch(
                "PycroFlow.gui.tabs.setup_tab.configs.save_setup",
                return_value="/x/Emulator.yaml",
            ) as save,
        ):
            tab._on_save()
        q.assert_not_called()
        save.assert_called_once()

    def test_empty_name_is_rejected(self):
        raw = configs.load_setup_file("Emulator")
        raw["setup"] = ""
        tab = self._tab()
        tab.load_data(raw)
        with self.assertRaises(ValueError):
            tab._validated_result()


class TestSetupEditorDialog(_QtSetupBase):
    def test_accept_returns_validated_data(self):
        from PycroFlow.gui.tabs.setup_tab import SetupEditorDialog

        raw = configs.load_setup_file("Emulator")
        raw["setup"] = "DlgRig"
        dlg = SetupEditorDialog(raw)
        dlg._on_accept()
        self.assertIsNotNone(dlg.result_data())
        self.assertEqual(dlg.result_data()["setup"], "DlgRig")
        self.assertEqual(
            dlg.result_data()["fluid"]["reservoirs"],
            raw["fluid"]["reservoirs"],
        )

    def test_invalid_setup_keeps_result_none(self):
        from PycroFlow.gui.tabs.setup_tab import SetupEditorDialog

        raw = configs.load_setup_file("Emulator")
        del raw["fluid"]["pumps"]["interface"]["COM"]  # required -> invalid
        dlg = SetupEditorDialog(raw)
        with mock.patch("PycroFlow.gui.tabs.setup_tab.QMessageBox.warning"):
            dlg._on_accept()
        self.assertIsNone(dlg.result_data())


class TestWizardSetupPageEditor(_QtSetupBase):
    def _page(self):
        from PycroFlow.gui.setup_wizard import _SetupPage

        return _SetupPage()

    def test_edited_data_supersedes_quick_fields(self):
        page = self._page()
        page.name.setText("WizRig")
        page._edited_data = {
            "setup": "ignored",
            "emulated": True,
            "fluid": {"custom": 1},
        }
        built = page._build()
        self.assertEqual(
            built["setup"], "WizRig"
        )  # name field stays authoritative
        self.assertEqual(built["fluid"], {"custom": 1})

    def test_quick_field_changes_clear_edited_data(self):
        # A detailed edit is a snapshot; changing any quick field afterwards
        # must discard it so the quick fields can't be silently ignored.
        for mutate in (
            lambda p: p.emulated.setChecked(True),
            lambda p: p.com.setCurrentText("7"),
            lambda p: p.monet_config.setText("Scope"),
            lambda p: p.template.setCurrentIndex(
                1 if p.template.currentIndex() != 1 else 0
            ),
        ):
            page = self._page()
            page._edited_data = {"setup": "x"}
            mutate(page)
            self.assertIsNone(page._edited_data)

    def test_quick_path_without_edit(self):
        page = self._page()
        page.name.setText("QuickRig")
        page.emulated.setChecked(True)
        built = page._build()
        self.assertEqual(built["setup"], "QuickRig")
        self.assertTrue(built["emulated"])


if __name__ == "__main__":
    unittest.main()
