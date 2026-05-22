"""Headless tests for the Stage 5 Qt GUI.

Run with the offscreen Qt platform so no display is needed. Skipped
entirely when PyQt5 is not installed (e.g. a minimal CI job without the
[gui] extra). These cover the wiring we can verify without a human: the
package is import-safe without PyQt5, the qt_bridge translates service
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
    import PyQt5  # noqa: F401
    _HAVE_PYQT5 = True
except ImportError:
    _HAVE_PYQT5 = False


def _import_safe_without_pyqt5():
    """import PycroFlow.gui must not import PyQt5 at package level."""
    mod = importlib.import_module('PycroFlow.gui')
    return mod


class TestGuiImportSafety(unittest.TestCase):

    def test_package_import_does_not_require_pyqt5(self):
        # Importing the package should succeed and must not pull PyQt5 in by
        # itself (the Qt-dependent modules import it lazily).
        before = 'PyQt5' in sys.modules
        _import_safe_without_pyqt5()
        # If PyQt5 wasn't already loaded, importing the package shouldn't
        # have loaded it. (When it was already loaded by another test, we
        # can't assert much — just that the import works.)
        if not before:
            self.assertNotIn('PyQt5', sys.modules)


@unittest.skipUnless(_HAVE_PYQT5, "PyQt5 not installed")
class TestQtBridge(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
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


@unittest.skipUnless(_HAVE_PYQT5, "PyQt5 not installed")
class TestMainWindow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow
        return PycroFlowMainWindow(ExperimentService(), SystemService())

    def test_builds_four_tabs(self):
        w = self._build()
        self.assertEqual(w.tabs.count(), 4)
        self.assertEqual(
            [w.tabs.tabText(i) for i in range(4)],
            ['Experiment', 'Fluid', 'Imaging', 'Monet'])

    def test_experiment_tab_reflects_state(self):
        from PycroFlow.examples.demo_protocols import protocol
        w = self._build()
        w.experiment_tab._service.load_protocol(protocol)
        # The bridge updates the label synchronously (same thread).
        self.assertEqual(w.experiment_tab.state_label.text(), 'loaded')
        # Step list populated from the fluid protocol entries.
        self.assertGreater(w.experiment_tab.step_list.count(), 0)

    def test_close_event_is_safe(self):
        from PyQt5.QtGui import QCloseEvent
        w = self._build()
        # Should not raise even with no protocol / no hardware.
        w.closeEvent(QCloseEvent())


@unittest.skipUnless(_HAVE_PYQT5, "PyQt5 not installed")
class TestMonetTab(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        sys.modules.pop('monet', None)
        sys.modules.pop('monet.gui', None)

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
        from PyQt5.QtWidgets import QWidget
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


if __name__ == '__main__':
    unittest.main()
