"""Headless tests for the Stage 5 Qt GUI.

Run with the offscreen Qt platform so no display is needed. Skipped
entirely when PyQt6 is not installed (e.g. a minimal CI job without the
[gui] extra). These cover the wiring we can verify without a human: the
package is import-safe without PyQt6, the qt_bridge translates service
observer callbacks into Qt signals, the main window builds with all four
tabs, and the monet tab degrades to a placeholder when monet is absent and
embeds a window when it's present.

What these do NOT cover (needs the lab Windows box with real instruments):
live acquisition, real laser control from the monet tab, and the
single-process MM Core conflict resolution.
"""
import importlib
import os
import sys
import types
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    import PyQt6  # noqa: F401
    _HAVE_PYQT6 = True
except ImportError:
    _HAVE_PYQT6 = False


def _import_safe_without_pyqt6():
    """import PycroFlow.gui must not import PyQt6 at package level."""
    mod = importlib.import_module('PycroFlow.gui')
    return mod


class TestGuiImportSafety(unittest.TestCase):

    def test_package_import_does_not_require_pyqt6(self):
        # Importing the package should succeed and must not pull PyQt6 in by
        # itself (the Qt-dependent modules import it lazily).
        before = 'PyQt6' in sys.modules
        _import_safe_without_pyqt6()
        # If PyQt6 wasn't already loaded, importing the package shouldn't
        # have loaded it. (When it was already loaded by another test, we
        # can't assert much — just that the import works.)
        if not before:
            self.assertNotIn('PyQt6', sys.modules)


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestQtBridge(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_state_observer_emits_signal(self):
        from PycroFlow.services import ExperimentService, ExperimentState
        from PycroFlow.gui.qt_bridge import QtBridge

        svc = ExperimentService()
        bridge = QtBridge(svc)
        received = []
        bridge.state_changed.connect(lambda o, n: received.append((o, n)))

        from PycroFlow.examples.demo_protocols import protocol
        svc.load_protocol(protocol)

        # Same-thread emission is synchronous under DirectConnection.
        self.assertEqual(
            received, [(ExperimentState.IDLE, ExperimentState.LOADED)])

    def test_log_observer_emits_signal(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.qt_bridge import QtBridge

        svc = ExperimentService()
        bridge = QtBridge(svc)
        lines = []
        bridge.log_message.connect(lambda m: lines.append(m))

        from PycroFlow.examples.demo_protocols import protocol
        svc.load_protocol(protocol)

        self.assertTrue(any('loaded' in m for m in lines))


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMainWindow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow
        return PycroFlowMainWindow(ExperimentService(), SystemService())

    def test_builds_tabs(self):
        w = self._build()
        self.assertEqual(w.tabs.count(), 5)
        self.assertEqual(
            [w.tabs.tabText(i) for i in range(5)],
            ['Experiment Design', 'Run Sequence', 'Fluid', 'Imaging',
             'Monet'])

    def test_window_title_has_version(self):
        from PycroFlow import __version__
        w = self._build()
        self.assertIn(__version__, w.windowTitle())

    def test_run_controls_live_in_run_sequence_tab(self):
        from PycroFlow.examples.demo_protocols import protocol
        from PycroFlow.services import ExperimentState
        w = self._build()
        tab = w.run_sequence_tab
        # Idle: only Load is available.
        self.assertTrue(tab.load_btn.isEnabled())
        self.assertFalse(tab.start_btn.isEnabled())
        self.assertFalse(tab.pause_resume_btn.isEnabled())
        self.assertFalse(tab.abort_btn.isEnabled())
        # Loaded: Start + Load available, Abort not.
        tab._service.load_protocol(protocol)
        self.assertTrue(tab.start_btn.isEnabled())
        self.assertTrue(tab.load_btn.isEnabled())
        self.assertFalse(tab.abort_btn.isEnabled())
        # Running: the toggle shows Pause; Load is disabled.
        tab._service._set_state(ExperimentState.RUNNING)
        self.assertEqual(tab.pause_resume_btn.text(), 'Pause')
        self.assertTrue(tab.pause_resume_btn.isEnabled())
        self.assertTrue(tab.abort_btn.isEnabled())
        self.assertFalse(tab.load_btn.isEnabled())
        # Paused: the same toggle shows Resume.
        tab._service._set_state(ExperimentState.PAUSED)
        self.assertEqual(tab.pause_resume_btn.text(), 'Resume')
        self.assertTrue(tab.pause_resume_btn.isEnabled())

    def test_pause_resume_toggle_calls_service(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab
        svc = MagicMock(name='service')
        svc.state = ExperimentState.RUNNING
        tab = ExperimentTab(svc, MagicMock(name='bridge'))
        tab._on_pause_resume()
        svc.pause.assert_called_once()
        svc.resume.assert_not_called()
        svc.state = ExperimentState.PAUSED
        tab._on_pause_resume()
        svc.resume.assert_called_once()

    def test_experiment_tab_reflects_state(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        w.run_sequence_tab._service.load_protocol(protocol)
        # The bridge updates the label synchronously (same thread).
        self.assertEqual(w.run_sequence_tab.state_label.text(), 'loaded')
        # Fluid step list populated from the fluid protocol entries.
        self.assertGreater(
            w.run_sequence_tab.step_lists['fluid'].count(), 0)

    @staticmethod
    def _table_dict(tab):
        return {
            tab.step_table.item(r, 0).text(): tab.step_table.item(r, 1).text()
            for r in range(tab.step_table.rowCount())
        }

    def test_experiment_tab_shows_step_parameters(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        tab = w.run_sequence_tab
        tab._service.load_protocol(protocol)
        self.assertGreater(tab.step_lists['fluid'].count(), 0)
        # Selecting a step shows that entry's parameters in the table.
        tab.step_lists['fluid'].setCurrentRow(0)
        entry = tab._entries['fluid'][0]
        shown = self._table_dict(tab)
        self.assertEqual(shown.get('$type'), str(entry['$type']))
        for key in entry:
            self.assertIn(key, shown)
        # The parameter box labels which list the step came from.
        self.assertIn('Fluid', tab.step_param_label.text())

    def test_close_event_is_safe(self):
        from PyQt6.QtGui import QCloseEvent
        w = self._build()
        # Should not raise even with no protocol / no hardware.
        w.closeEvent(QCloseEvent())

    def test_progress_bars_and_step_shading(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import (
            ExperimentTab, _FINISHED_COLOR, _ACTIVE_COLOR)
        svc = MagicMock(name='service')
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            'fluid': {'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 10},
                {'$type': 'signal', 'value': 'x'},
                {'$type': 'incubate', 'duration': 1}]},
            'img': {'protocol_entries': [
                {'$type': 'acquire', 'frames': 1, 't_exp': 1},
                {'$type': 'acquire', 'frames': 1, 't_exp': 1}]},
            'illu': {'protocol_entries': []},
        }
        svc.progress.return_value = {
            'fluid': (1, 3), 'img': (1, 2), 'illu': (0, 0)}
        tab = ExperimentTab(svc, MagicMock(name='bridge'))
        tab._populate_steps()
        tab._poll_progress()
        # overall: done 1+1+0=2 / total 3+2+0=5 = 40%, count shown on the right
        self.assertEqual(tab.overall_bar.value(), 40)
        self.assertEqual(tab.overall_count.text(), '2/5')
        # rounds: 2 acquires, img cur=1 -> 1 done (idx 0) of 2 = 50%
        self.assertEqual(tab.round_bar.value(), 50)
        self.assertEqual(tab.round_count.text(), '1/2')
        # per-subsystem status: centered, current step name in brackets
        from PyQt6.QtCore import Qt
        # fluid cur=1 -> entries[1] is the 'signal' step
        self.assertIn('fluid 1/3 (signal)', tab.step_status.text())
        self.assertTrue(
            bool(tab.step_status.alignment() & Qt.AlignmentFlag.AlignCenter))
        # fluid rows: 0 finished, 1 active (== cur), 2 pending
        fluid = tab.step_lists['fluid']
        self.assertEqual(
            fluid.item(0).background().color(), _FINISHED_COLOR)
        self.assertEqual(
            fluid.item(1).background().color(), _ACTIVE_COLOR)

    def test_current_round_progress_bar(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab
        svc = MagicMock(name='service')
        svc.state = ExperimentState.RUNNING
        # Two rounds: each fluid round ends at a wait-for-img; each img round
        # ends at an acquire.
        svc.protocol = {
            'fluid': {'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 10},
                {'$type': 'signal', 'value': 'done flushing r0'},
                {'$type': 'wait for signal', 'target': 'img', 'value': 'a'},
                {'$type': 'inject', 'reservoir_id': 2, 'volume': 10},
                {'$type': 'signal', 'value': 'done flushing r1'},
                {'$type': 'wait for signal', 'target': 'img', 'value': 'b'}]},
            'img': {'protocol_entries': [
                {'$type': 'acquire', 'frames': 1, 't_exp': 1},
                {'$type': 'signal', 'value': 'done imaging r0'},
                {'$type': 'acquire', 'frames': 1, 't_exp': 1},
                {'$type': 'signal', 'value': 'done imaging r1'}]},
            'illu': {'protocol_entries': []},
        }
        # Mid round 0: fluid on step 1 of {0,1,2}, img acquire not yet done.
        svc.progress.return_value = {
            'fluid': (1, 6), 'img': (0, 4), 'illu': (0, 0)}
        tab = ExperimentTab(svc, MagicMock(name='bridge'))
        tab._populate_steps()
        tab._poll_progress()
        # Round-0 steps: fluid 0,1,2 + img 0 = 4 total; done = fluid step 0.
        self.assertEqual(tab.current_round_bar.value(), 25)
        self.assertEqual(tab.current_round_count.text(), '1/4')

    def test_within_step_bars(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab
        svc = MagicMock(name='service')
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            'fluid': {'protocol_entries': [{'$type': 'incubate'}]},
            'img': {'protocol_entries': [{'$type': 'acquire'}]},
            'illu': {'protocol_entries': []},
        }
        svc.progress.return_value = {
            'fluid': (0, 1), 'img': (0, 1), 'illu': (0, 0)}
        # Imaging mid-acquisition; fluid incubating; illu nothing.
        svc.step_progress.return_value = {
            'img': (200, 500, 'frames'),
            'fluid': (12.0, 30.0, 'incubate'),
            'illu': None,
        }
        tab = ExperimentTab(svc, MagicMock(name='bridge'))
        tab._populate_steps()
        tab._poll_progress()
        img_bar, img_count = tab.substep_bars['img'][1:]
        # isHidden() reflects the explicit show/hide flag (isVisible() needs a
        # shown top-level window, which offscreen tests don't have).
        self.assertFalse(img_bar.isHidden())
        self.assertEqual(img_bar.value(), 40)
        self.assertEqual(img_count.text(), 'frames 200/500')
        fluid_count = tab.substep_bars['fluid'][2]
        self.assertEqual(fluid_count.text(), 'incubate 12/30 s')
        # Illumination has no sub-progress -> its row stays hidden.
        self.assertTrue(tab.substep_bars['illu'][1].isHidden())

    @staticmethod
    def _sync_protocol():
        # One round: fluid flushes then signals; img waits, acquires, signals;
        # fluid then waits for imaging.
        return {
            'fluid': {'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 10},
                {'$type': 'signal', 'value': 'flush0'},
                {'$type': 'wait for signal', 'target': 'img',
                 'value': 'img0'},
                {'$type': 'inject', 'reservoir_id': 2, 'volume': 10}]},
            'img': {'protocol_entries': [
                {'$type': 'wait for signal', 'target': 'fluid',
                 'value': 'flush0'},
                {'$type': 'acquire', 'frames': 1, 't_exp': 1},
                {'$type': 'signal', 'value': 'img0'}]},
            'illu': {'protocol_entries': []},
        }

    def test_step_correlation_across_systems(self):
        w = self._build()
        tab = w.run_sequence_tab
        tab._service.load_protocol(self._sync_protocol())
        # Click the fluid round-0 inject -> img is parked at its wait.
        tab.step_lists['fluid'].setCurrentRow(0)
        self.assertEqual(tab.step_lists['img'].currentRow(), 0)
        # Click the img acquire -> fluid is blocked at its wait-for-imaging.
        tab.step_lists['img'].setCurrentRow(1)
        self.assertEqual(tab.step_lists['fluid'].currentRow(), 2)
        # The parameter box still reflects the clicked (img) step.
        self.assertIn('Imaging', tab.step_param_label.text())

    def test_center_button_enabled_only_while_running(self):
        from PycroFlow.services import ExperimentState
        w = self._build()
        tab = w.run_sequence_tab
        tab._service.load_protocol(self._sync_protocol())
        self.assertFalse(tab.center_btn.isEnabled())   # loaded, not running
        tab._service._set_state(ExperimentState.RUNNING)
        self.assertTrue(tab.center_btn.isEnabled())
        # Centring must not raise (scrolls each list to its current step).
        tab._center_on_current()

    def test_experiment_tab_lists_all_subsystem_steps(self):
        w = self._build()
        proto = {
            'fluid': {'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 100}]},
            'img': {'protocol_entries': [
                {'$type': 'acquire', 'frames': 10, 't_exp': 100}]},
            'illu': {'protocol_entries': [
                {'$type': 'set power', 'laser': 560, 'power': 30}]},
        }
        tab = w.run_sequence_tab
        tab._service.load_protocol(proto)
        # Each subsystem has its own list.
        self.assertEqual(
            [tab.step_lists['fluid'].item(0).text()], ['0: inject'])
        self.assertEqual(
            [tab.step_lists['img'].item(0).text()], ['0: acquire'])
        self.assertEqual(
            [tab.step_lists['illu'].item(0).text()], ['0: set power'])
        # Selecting the img step shows its parameters, labelled "Imaging".
        tab.step_lists['img'].setCurrentRow(0)
        shown = self._table_dict(tab)
        self.assertEqual(shown['$type'], 'acquire')
        self.assertEqual(shown['frames'], '10')
        self.assertEqual(shown['t_exp'], '100')
        self.assertIn('Imaging', tab.step_param_label.text())
        # With no signals the lone steps are concurrent, so clicking img
        # correlates the other lists to their step 0.
        self.assertEqual(tab.step_lists['fluid'].currentRow(), 0)
        self.assertEqual(tab.step_lists['illu'].currentRow(), 0)

    def _load_inject(self, tab):
        proto = {'fluid': {'protocol_entries': [
            {'$type': 'inject', 'reservoir_id': 1, 'volume': 100}]}}
        tab._service.load_protocol(proto)
        tab.step_lists['fluid'].setCurrentRow(0)

    def _set_cell(self, tab, key, text):
        for r in range(tab.step_table.rowCount()):
            if tab.step_table.item(r, 0).text() == key:
                tab.step_table.item(r, 1).setText(text)
                return
        self.fail("no row for key {!r}".format(key))

    def test_experiment_tab_edit_writes_back_to_protocol(self):
        w = self._build()
        tab = w.run_sequence_tab
        self._load_inject(tab)
        self._set_cell(tab, 'volume', '250')
        tab._on_apply()
        # _entries['fluid'][0] is a reference into the loaded protocol, so both
        # the cached entry and the service's protocol are updated, as int.
        self.assertEqual(tab._entries['fluid'][0]['volume'], 250)
        stored = tab._service.protocol['fluid']['protocol_entries'][0]
        self.assertEqual(stored['volume'], 250)
        self.assertIsInstance(stored['volume'], int)

    def test_experiment_tab_invalid_edit_reported_and_skipped(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import experiment_tab as et
        w = self._build()
        tab = w.run_sequence_tab
        self._load_inject(tab)
        self._set_cell(tab, 'volume', 'not-a-number')
        with patch.object(et.QMessageBox, 'warning') as warn:
            tab._on_apply()
        warn.assert_called_once()
        self.assertEqual(tab._entries['fluid'][0]['volume'], 100)

    def test_experiment_tab_type_field_not_editable(self):
        from PyQt6.QtCore import Qt
        w = self._build()
        tab = w.run_sequence_tab
        self._load_inject(tab)
        for r in range(tab.step_table.rowCount()):
            if tab.step_table.item(r, 0).text() == '$type':
                flags = tab.step_table.item(r, 1).flags()
                self.assertFalse(bool(flags & Qt.ItemFlag.ItemIsEditable))
                return
        self.fail("no $type row")


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMonetTab(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        sys.modules.pop('monet', None)
        sys.modules.pop('monet.gui', None)
        # Restore the shared hardware mocks so later tests that import
        # PycroFlow.illumination / monet still find a mocked monet (these
        # tests pop it to exercise the absent / present paths).
        from PycroFlow.tests._mock_hardware import install_hardware_mocks
        install_hardware_mocks()

    def test_placeholder_when_monet_absent(self):
        sys.modules.pop('monet', None)
        sys.modules.pop('monet.gui', None)
        from PycroFlow.gui.tabs.monet_tab import MonetTab
        # Force the import inside MonetTab to fail by inserting a stub that
        # raises on attribute access of gui.
        tab = MonetTab()
        # No monet -> no embedded window.
        self.assertIsNone(tab._monet_window)
        # shutdown must be safe with no embedded window.
        tab.shutdown()

    def test_embeds_when_monet_present(self):
        from PyQt6.QtWidgets import QWidget
        # Inject a fake monet.gui.MonetMainWindow that is a real QWidget.
        fake_monet = types.ModuleType('monet')
        fake_gui = types.ModuleType('monet.gui')

        class FakeMonetWindow(QWidget):
            def __init__(self):
                super().__init__()
                self.closed = False

            def close(self):
                self.closed = True
                return super().close()

        fake_gui.MonetMainWindow = FakeMonetWindow
        fake_monet.gui = fake_gui
        sys.modules['monet'] = fake_monet
        sys.modules['monet.gui'] = fake_gui

        from PycroFlow.gui.tabs.monet_tab import MonetTab
        tab = MonetTab()
        self.assertIsInstance(tab._monet_window, FakeMonetWindow)
        # shutdown triggers monet's cleanup (close()).
        tab.shutdown()
        self.assertTrue(tab._monet_window.closed)


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestConnectionFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(True)

    def tearDown(self):
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(False)

    def _win(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow
        return PycroFlowMainWindow(ExperimentService(), SystemService())

    def test_startup_loads_setup_and_drives_monet(self):
        w = self._win()
        self.assertIsNotNone(w._system_service.setup)
        self.assertEqual(
            w._system_service.get_monet_setup(),
            w.setup_combo.currentText())

    def test_autoconnect_on_design_load(self):
        import PycroFlow
        w = self._win()
        w._on_setup_changed('Emulator')
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__), 'examples',
            'sph_resi_6plex.yaml')
        w.design_tab.load_design_path(path)
        self.assertEqual(
            w._system_service.connection_states(),
            {'fluid': True, 'imaging': True, 'illumination': True})
        self.assertEqual(w.fluid_tab.status_label.text(), 'connected')
        self.assertEqual(w.imaging_tab.status_label.text(), 'connected')
        self.assertIsNotNone(w._experiment_service._fluid_system)

    def test_status_bar_confirms_connections(self):
        import PycroFlow
        w = self._win()
        w._on_setup_changed('Emulator')
        # Before connecting: setup shown, systems not connected.
        text = w.status_label.text()
        self.assertIn('Setup: Emulator', text)
        self.assertIn('not connected', text)
        # After autoconnect (via design load): each system confirmed.
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__), 'examples',
            'sph_resi_6plex.yaml')
        w.design_tab.load_design_path(path)
        text = w.status_label.text()
        self.assertIn('Fluid: ✓ connected', text)
        self.assertIn('Imaging: ✓ connected', text)
        self.assertIn('Illumination: ✓ connected', text)
        self.assertNotIn('not connected', text)

    def test_fluid_connect_requires_design(self):
        from unittest.mock import patch
        from PycroFlow.gui import main_window as mw
        w = self._win()
        with patch.object(mw.QMessageBox, 'warning') as warn:
            w.fluid_tab._on_connect_clicked()
        warn.assert_called_once()
        self.assertIsNone(w._system_service.fluid_system)

    def test_manual_imaging_connect(self):
        w = self._win()
        w._on_setup_changed('Emulator')
        w.imaging_tab._on_connect_clicked()
        self.assertIsNotNone(w._system_service.imaging_system)
        self.assertEqual(w.imaging_tab.status_label.text(), 'connected')

    def test_hardware_locked_during_run(self):
        from PycroFlow.services import ExperimentState
        w = self._win()
        self.assertTrue(w.fluid_tab.connect_btn.isEnabled())
        self.assertTrue(w.setup_combo.isEnabled())

        # Entering a running state locks manual hardware access.
        w._experiment_service._set_state(ExperimentState.RUNNING)
        self.assertFalse(w.fluid_tab.connect_btn.isEnabled())
        self.assertFalse(w.imaging_tab.connect_btn.isEnabled())
        self.assertFalse(w.monet_tab._embed_container.isEnabled())
        self.assertFalse(w.setup_combo.isEnabled())
        self.assertFalse(w.act_connect.isEnabled())
        # STOP stays available during a run.
        self.assertTrue(w.fluid_tab.stop_btn.isEnabled())

        # Leaving the run unlocks everything again.
        w._experiment_service._set_state(ExperimentState.ABORTED)
        self.assertTrue(w.fluid_tab.connect_btn.isEnabled())
        self.assertTrue(w.monet_tab._embed_container.isEnabled())
        self.assertTrue(w.setup_combo.isEnabled())


def _example_design():
    import PycroFlow
    from PycroFlow.services import ExperimentService
    path = os.path.join(
        os.path.dirname(PycroFlow.__file__), 'examples', 'sph_resi_6plex.yaml')
    return ExperimentService().load_experiment_design(path), path


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestSchemaForm(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_roundtrip_and_validate(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import ExperimentDesign
        design, _ = _example_design()
        form = SchemaForm(ExperimentDesign, design)
        model = form.to_model()
        self.assertEqual(model.fluid.settings.experiment.type, 'SPH-RESI')
        d = form.to_dict()
        tr = d['fluid']['settings']['experiment']['target-rounds']['A1']
        self.assertEqual(len(tr['RESI-rounds']), 6)

    def test_list_model_editor_add_remove(self):
        from PycroFlow.gui.widgets.schema_form import _ListModelEditor
        from PycroFlow.schemas.experiment_design import ResiRound
        ed = _ListModelEditor(
            ResiRound, [{'adapter': 'a', 'adapter_incubation': 1}], 'RESI')
        self.assertEqual(len(ed.get_value()), 1)
        ed._add_item({'adapter': 'b', 'adapter_incubation': 2})
        self.assertEqual(len(ed.get_value()), 2)
        ed._remove(ed._items[0])
        self.assertEqual(len(ed.get_value()), 1)
        self.assertEqual(ed.get_value()[0]['adapter'], 'b')

    def test_scalar_defaults_seeded(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidParameters
        form = SchemaForm(FluidParameters, {})
        self.assertEqual(form.to_dict()['mode'], 'tubing_ignore')


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestExperimentDesignTab(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_load_and_translate(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab)
        _, path = _example_design()
        svc = ExperimentService()
        translated = []
        tab = ExperimentDesignTab(
            svc, on_translated=lambda: translated.append(1))
        tab.load_design_path(path)
        self.assertEqual(
            svc.experiment_design['fluid']['settings']['experiment']['type'],
            'SPH-RESI')
        tab._on_translate()
        self.assertEqual(svc.state.value, 'loaded')
        self.assertEqual(translated, [1])

    def test_drag_drop_loads_design(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab)
        _, path = _example_design()
        svc = ExperimentService()
        tab = ExperimentDesignTab(svc)
        tab.on_yaml_dropped(path)
        self.assertIsNotNone(svc.experiment_design)

    def test_save_dir_absolute_path_hint(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab)
        tab = ExperimentDesignTab(ExperimentService())
        editor = tab._form.field_editor('save_dir')
        line = editor.line_edit()
        # Relative path -> hint shows the resolved absolute destination.
        line.setText('subdir')
        self.assertEqual(
            tab._save_dir_hint.text(),
            '→ {}'.format(os.path.abspath('subdir')))
        # '.' resolves to the working directory.
        line.setText('.')
        self.assertEqual(
            tab._save_dir_hint.text(), '→ {}'.format(os.path.abspath('.')))
        # Absolute path -> no hint shown.
        line.setText(os.path.abspath('subdir'))
        self.assertEqual(tab._save_dir_hint.text(), '')


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMonetSetSetup(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        sys.modules.pop('monet', None)
        sys.modules.pop('monet.gui', None)
        from PycroFlow.tests._mock_hardware import install_hardware_mocks
        install_hardware_mocks()

    def test_set_setup_preselects_without_autoconnect(self):
        from PyQt6.QtWidgets import QWidget, QComboBox
        fake_monet = types.ModuleType('monet')
        fake_gui = types.ModuleType('monet.gui')

        class FakeMonetWidget(QWidget):
            def __init__(self, initial_microscope=None):
                super().__init__()
                self.initial_microscope = initial_microscope
                self._scope_combo = QComboBox()
                self._scope_combo.addItems(['Emulator', 'Mercury'])

        fake_gui.MonetWidget = FakeMonetWidget
        fake_monet.gui = fake_gui
        sys.modules['monet'] = fake_monet
        sys.modules['monet.gui'] = fake_gui

        from PycroFlow.gui.tabs.monet_tab import MonetTab
        tab = MonetTab()
        tab.set_setup('Mercury')
        # No auto-connect: initial_microscope is NOT passed (avoids fighting
        # PycroFlow's IlluminationSystem for the laser COM port).
        self.assertIsNone(tab._monet_window.initial_microscope)
        # The scope is pre-selected for display.
        self.assertEqual(
            tab._monet_window._scope_combo.currentText(), 'Mercury')


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestFluidTab(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(True)

    def tearDown(self):
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(False)

    def _tab(self):
        from unittest.mock import MagicMock
        from PycroFlow.gui.tabs.fluid_tab import FluidTab
        svc = MagicMock(name='system_service')
        svc.fluid_system = object()
        return FluidTab(svc), svc

    def test_fill_calls_service(self):
        tab, svc = self._tab()
        tab._on_fill()
        svc.fill_tubings.assert_called_once()

    def test_clean_confirm_yes_calls_service(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft
        tab, svc = self._tab()
        with patch.object(ft.QMessageBox, 'question',
                          return_value=ft.QMessageBox.StandardButton.Yes):
            tab._on_clean()
        svc.clean_tubings.assert_called_once()

    def test_clean_confirm_no_does_nothing(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft
        tab, svc = self._tab()
        with patch.object(ft.QMessageBox, 'question',
                          return_value=ft.QMessageBox.StandardButton.No):
            tab._on_clean()
        svc.clean_tubings.assert_not_called()

    def test_stroke_calls_manual_pump(self):
        tab, svc = self._tab()
        tab.stroke_pump.setCurrentText('pump_a')
        tab.stroke_vol.setText('150')
        tab.stroke_vel.setText('200')
        tab.stroke_pickup.setCurrentText('in')
        tab.stroke_dispense.setCurrentText('out')
        tab._on_stroke()
        svc.manual_pump.assert_called_once_with(
            'pump_a', vol=150.0, pickup_dir='in', dispense_dir='out',
            velocity=200.0)

    def test_move_includes_reservoirs(self):
        tab, svc = self._tab()
        tab.move_pump.setCurrentText('pump_a')
        tab.move_vol.setText('80')
        tab.move_pickup_res.setText('5')
        tab.move_dispense_res.setText('7')
        tab.move_pickup_dir.setCurrentText('in')
        tab.move_dispense_dir.setCurrentText('in')
        tab._on_move()
        svc.manual_pump.assert_called_once_with(
            'pump_a', vol=80.0, pickup_dir='in', dispense_dir='in',
            pickup_res=5, dispense_res=7)

    def test_set_valves_calls_service(self):
        tab, svc = self._tab()
        tab.valve_res.setText('3')
        tab._on_set_valves()
        svc.set_valves.assert_called_once_with(3)

    def test_set_valves_required_empty_warns(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft
        tab, svc = self._tab()
        tab.valve_res.setText('')
        with patch.object(ft.QMessageBox, 'warning') as warn:
            tab._on_set_valves()
        warn.assert_called_once()
        svc.set_valves.assert_not_called()

    def test_stop_calls_service(self):
        tab, svc = self._tab()
        tab._on_stop()
        svc.stop_all_moves.assert_called_once()


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestWorker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _pump_until(self, predicate, timeout=5.0):
        import time
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def test_runs_off_thread_and_calls_on_done(self):
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtCore import QThread
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(False)
        owner = QWidget()
        results = []
        threads = []

        def work():
            threads.append(QThread.currentThread())
            return 21 * 2

        worker.run_in_background(owner, work, on_done=results.append)
        self._pump_until(lambda: results)
        self.assertEqual(results, [42])
        # ran on a different thread than the GUI thread
        self.assertIsNot(threads[0], self.app.thread())

    def test_on_error_called_for_exception(self):
        from PyQt6.QtWidgets import QWidget
        from PycroFlow.gui.widgets import worker
        worker.set_synchronous(False)
        owner = QWidget()
        errors = []

        def boom():
            raise RuntimeError("nope")

        worker.run_in_background(owner, boom, on_error=errors.append)
        self._pump_until(lambda: errors)
        self.assertIsInstance(errors[0], RuntimeError)


if __name__ == '__main__':
    unittest.main()
