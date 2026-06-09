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
            ['System', 'Experiment', 'Fluid', 'Imaging', 'Monet'])

    def test_experiment_tab_reflects_state(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        w.experiment_tab._service.load_protocol(protocol)
        # The bridge updates the label synchronously (same thread).
        self.assertEqual(w.experiment_tab.state_label.text(), 'loaded')
        # Step list populated from the fluid protocol entries.
        self.assertGreater(w.experiment_tab.step_list.count(), 0)

    def test_experiment_tab_shows_step_parameters(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        tab = w.experiment_tab
        tab._service.load_protocol(protocol)
        self.assertGreater(tab.step_list.count(), 0)
        # Selecting a step shows that entry's parameters in the side box.
        tab.step_list.setCurrentRow(0)
        entry = tab._step_entries[0]
        shown = tab.step_params.toPlainText()
        self.assertIn("$type: {}".format(entry['$type']), shown)
        for key, value in entry.items():
            if key == '$type':
                continue
            self.assertIn("{}: {}".format(key, value), shown)

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
        tab = w.experiment_tab
        tab._service.load_protocol(proto)
        items = [tab.step_list.item(i).text()
                 for i in range(tab.step_list.count())]
        self.assertEqual(items, [
            '[fluid] 0: inject',
            '[img] 0: acquire',
            '[illu] 0: set power',
        ])
        # Selecting the img step shows its parameters.
        tab.step_list.setCurrentRow(1)
        shown = tab.step_params.toPlainText()
        self.assertIn('$type: acquire', shown)
        self.assertIn('frames: 10', shown)
        self.assertIn('t_exp: 100', shown)


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

    def _svc(self, **connected):
        from unittest.mock import MagicMock
        svc = MagicMock(name='system_service')
        svc.fluid_system = connected.get('fluid')
        svc.imaging_system = connected.get('imaging')
        svc.illumination_system = connected.get('illumination')

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

    def test_connect_imaging_uses_file_dialog(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc()

        def _connect(path):
            svc.imaging_system = object()
        svc.connect_imaging.side_effect = _connect

        tab = st.SystemTab(svc)
        with patch.object(st.QFileDialog, 'getOpenFileName',
                          return_value=('/tmp/imaging_config.yaml', '')):
            tab._on_connect_imaging()

        svc.connect_imaging.assert_called_once_with(
            '/tmp/imaging_config.yaml')
        self.assertEqual(
            tab._status_labels['imaging'].text(), 'Connected')

    def test_connect_cancelled_does_nothing(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import system_tab as st
        svc = self._svc()
        tab = st.SystemTab(svc)
        with patch.object(st.QFileDialog, 'getOpenFileName',
                          return_value=('', '')):
            tab._on_connect_imaging()
        svc.connect_imaging.assert_not_called()

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


if __name__ == '__main__':
    unittest.main()
