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
        self.assertEqual(w.tabs.count(), 6)
        self.assertEqual(
            [w.tabs.tabText(i) for i in range(6)],
            ['System', 'Experiment Design', 'Run Sequence', 'Fluid',
             'Imaging', 'Monet'])

    def test_experiment_tab_reflects_state(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        w.run_sequence_tab._service.load_protocol(protocol)
        # The bridge updates the label synchronously (same thread).
        self.assertEqual(w.run_sequence_tab.state_label.text(), 'loaded')
        # Step list populated from the fluid protocol entries.
        self.assertGreater(w.run_sequence_tab.step_list.count(), 0)

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
        self.assertGreater(tab.step_list.count(), 0)
        # Selecting a step shows that entry's parameters in the table.
        tab.step_list.setCurrentRow(0)
        entry = tab._step_entries[0]
        shown = self._table_dict(tab)
        self.assertEqual(shown.get('$type'), str(entry['$type']))
        for key in entry:
            self.assertIn(key, shown)

    def test_close_event_is_safe(self):
        from PyQt6.QtGui import QCloseEvent
        w = self._build()
        # Should not raise even with no protocol / no hardware.
        w.closeEvent(QCloseEvent())

    def test_subsystem_tabs_sync_via_system_tab_listeners(self):
        w = self._build()
        # Both tabs start "not connected".
        self.assertEqual(w.fluid_tab.status_label.text(), 'not connected')
        self.assertEqual(w.imaging_tab.status_label.text(), 'not connected')
        # Simulate hardware connected on the shared service, then fire the
        # listeners the System tab invokes after a successful connect.
        w._system_service.fluid_system = object()
        w._system_service.imaging_system = object()
        self.assertTrue(w.system_tab._connection_listeners)
        for fn in w.system_tab._connection_listeners:
            fn()
        self.assertEqual(w.fluid_tab.status_label.text(), 'connected')
        self.assertEqual(w.imaging_tab.status_label.text(), 'connected')

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
        items = [tab.step_list.item(i).text()
                 for i in range(tab.step_list.count())]
        self.assertEqual(items, [
            '[fluid] 0: inject',
            '[img] 0: acquire',
            '[illu] 0: set power',
        ])
        # Selecting the img step shows its parameters in the table.
        tab.step_list.setCurrentRow(1)
        shown = self._table_dict(tab)
        self.assertEqual(shown['$type'], 'acquire')
        self.assertEqual(shown['frames'], '10')
        self.assertEqual(shown['t_exp'], '100')

    def _load_inject(self, tab):
        proto = {'fluid': {'protocol_entries': [
            {'$type': 'inject', 'reservoir_id': 1, 'volume': 100}]}}
        tab._service.load_protocol(proto)
        tab.step_list.setCurrentRow(0)

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
        # _step_entries[0] is a reference into the loaded protocol, so both
        # the cached entry and the service's protocol are updated, as int.
        self.assertEqual(tab._step_entries[0]['volume'], 250)
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
        self.assertEqual(tab._step_entries[0]['volume'], 100)

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
class TestSystemTab(unittest.TestCase):

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

    def _svc(self, **connected):
        from unittest.mock import MagicMock
        svc = MagicMock(name='system_service')
        svc.fluid_system = connected.get('fluid')
        svc.imaging_system = connected.get('imaging')
        svc.illumination_system = connected.get('illumination')
        # A setup is loaded by default (truthy); tests that need "no setup"
        # set svc.setup = None.
        svc.setup = connected.get('setup', object())
        svc.is_emulated.return_value = connected.get('emulated', False)

        def _states():
            return {
                'fluid': svc.fluid_system is not None,
                'imaging': svc.imaging_system is not None,
                'illumination': svc.illumination_system is not None,
            }
        svc.connection_states.side_effect = _states
        return svc

    def test_refresh_reflects_states(self):
        from PycroFlow.gui.tabs.system_tab import SystemTab
        tab = SystemTab(self._svc(imaging=object()))
        self.assertEqual(tab._status_labels['imaging'].text(), 'Connected')
        self.assertEqual(
            tab._status_labels['fluid'].text(), 'Not connected')

    def test_connect_illumination_calls_service_and_mirrors(self):
        from unittest.mock import MagicMock
        from PycroFlow.gui.tabs.system_tab import SystemTab
        svc = self._svc()
        exp = MagicMock(name='experiment_service')

        def _connect():
            svc.illumination_system = object()
        svc.connect_illumination.side_effect = _connect

        tab = SystemTab(svc, exp)
        tab._on_connect_illumination()

        svc.connect_illumination.assert_called_once()
        exp.attach_systems.assert_called_once()
        self.assertEqual(
            tab._status_labels['illumination'].text(), 'Connected')

    def test_connect_imaging_emulated_calls_service(self):
        from PycroFlow.gui.tabs.system_tab import SystemTab
        svc = self._svc(emulated=True)

        def _connect(*a, **k):
            svc.imaging_system = object()
        svc.connect_imaging.side_effect = _connect

        tab = SystemTab(svc)
        tab._on_connect_imaging()
        svc.connect_imaging.assert_called_once()
        self.assertEqual(
            tab._status_labels['imaging'].text(), 'Connected')

    def test_connect_requires_setup(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc(setup=None)
        tab = st.SystemTab(svc)
        with patch.object(st.QMessageBox, 'warning') as warn:
            tab._on_connect_illumination()
        warn.assert_called_once()
        svc.connect_illumination.assert_not_called()

    def test_connect_fluid_requires_design(self):
        from unittest.mock import MagicMock, patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc()
        exp = MagicMock(name='experiment_service')
        exp.experiment_design = None
        tab = st.SystemTab(svc, exp)
        with patch.object(st.QMessageBox, 'warning') as warn:
            tab._on_connect_fluid()
        warn.assert_called_once()
        svc.connect_fluid.assert_not_called()

    def test_connect_fluid_passes_design_fluid_section(self):
        from unittest.mock import MagicMock
        from PycroFlow.gui.tabs.system_tab import SystemTab
        svc = self._svc()
        exp = MagicMock(name='experiment_service')
        fluid = {'settings': {'reservoir_names': {1: 'R1'}},
                 'parameters': {'max_velocity': 200}}
        exp.experiment_design = {'fluid': fluid}

        def _connect(arg):
            svc.fluid_system = object()
        svc.connect_fluid.side_effect = _connect

        tab = SystemTab(svc, exp)
        tab._on_connect_fluid()
        svc.connect_fluid.assert_called_once_with(fluid)
        self.assertEqual(tab._status_labels['fluid'].text(), 'Connected')

    def test_load_setup_fires_setup_listener(self):
        from PycroFlow.gui.tabs.system_tab import SystemTab
        svc = self._svc()
        svc.get_monet_setup.return_value = 'Emulator'
        tab = SystemTab(svc)
        seen = []
        tab.add_setup_listener(lambda name: seen.append(name))
        tab.setup_combo.setCurrentText('Emulator')
        tab._on_load_setup()
        svc.load_setup.assert_called_once_with('Emulator')
        self.assertEqual(seen, ['Emulator'])

    def test_connect_failure_shows_message_and_stays_disconnected(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc()
        svc.connect_illumination.side_effect = RuntimeError("boom")
        tab = st.SystemTab(svc)
        with patch.object(st.QMessageBox, 'critical') as crit:
            tab._on_connect_illumination()
        crit.assert_called_once()
        self.assertEqual(
            tab._status_labels['illumination'].text(), 'Not connected')

    def test_connection_listener_fires_on_connect(self):
        from PycroFlow.gui.tabs.system_tab import SystemTab
        svc = self._svc()

        def _connect():
            svc.illumination_system = object()
        svc.connect_illumination.side_effect = _connect

        tab = SystemTab(svc)
        calls = []
        tab.add_connection_listener(lambda: calls.append(True))
        tab._on_connect_illumination()
        self.assertEqual(calls, [True])

    def test_connection_listener_not_fired_on_failure(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc()
        svc.connect_illumination.side_effect = RuntimeError("boom")
        tab = st.SystemTab(svc)
        calls = []
        tab.add_connection_listener(lambda: calls.append(True))
        with patch.object(st.QMessageBox, 'critical'):
            tab._on_connect_illumination()
        self.assertEqual(calls, [])


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

    def test_set_setup_passes_microscope(self):
        from PyQt6.QtWidgets import QWidget
        fake_monet = types.ModuleType('monet')
        fake_gui = types.ModuleType('monet.gui')

        class FakeMonetWidget(QWidget):
            def __init__(self, initial_microscope=None):
                super().__init__()
                self.initial_microscope = initial_microscope

        fake_gui.MonetWidget = FakeMonetWidget
        fake_monet.gui = fake_gui
        sys.modules['monet'] = fake_monet
        sys.modules['monet.gui'] = fake_gui

        from PycroFlow.gui.tabs.monet_tab import MonetTab
        tab = MonetTab()
        tab.set_setup('Mercury')
        self.assertEqual(tab._monet_window.initial_microscope, 'Mercury')


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
