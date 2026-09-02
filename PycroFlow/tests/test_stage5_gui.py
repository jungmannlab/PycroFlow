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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt6  # noqa: F401

    _HAVE_PYQT6 = True
except ImportError:
    _HAVE_PYQT6 = False


def _import_safe_without_pyqt6():
    """import PycroFlow.gui must not import PyQt6 at package level."""
    mod = importlib.import_module("PycroFlow.gui")
    return mod


class TestGuiImportSafety(unittest.TestCase):

    def test_package_import_does_not_require_pyqt6(self):
        # Importing the package should succeed and must not pull PyQt6 in by
        # itself (the Qt-dependent modules import it lazily).
        before = "PyQt6" in sys.modules
        _import_safe_without_pyqt6()
        # If PyQt6 wasn't already loaded, importing the package shouldn't
        # have loaded it. (When it was already loaded by another test, we
        # can't assert much — just that the import works.)
        if not before:
            self.assertNotIn("PyQt6", sys.modules)


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
            received, [(ExperimentState.IDLE, ExperimentState.LOADED)]
        )

    def test_log_observer_emits_signal(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.qt_bridge import QtBridge

        svc = ExperimentService()
        bridge = QtBridge(svc)
        lines = []
        bridge.log_message.connect(lambda m: lines.append(m))

        from PycroFlow.examples.demo_protocols import protocol

        svc.load_protocol(protocol)

        self.assertTrue(any("loaded" in m for m in lines))


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMainWindow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # Loading a design chdirs to its folder; keep test output out of the
        # checkout for whatever runs next.
        from PycroFlow.tests import chdir_to_test_output
        chdir_to_test_output()

    def _build(self):
        from PycroFlow.services import ExperimentService, SystemService
        from PycroFlow.gui.main_window import PycroFlowMainWindow

        return PycroFlowMainWindow(ExperimentService(), SystemService())

    def test_builds_tabs(self):
        w = self._build()
        self.assertEqual(w.tabs.count(), 5)
        self.assertEqual(
            [w.tabs.tabText(i) for i in range(5)],
            ["Experiment Design", "Run Sequence", "Fluid", "Imaging", "Monet"],
        )

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
        self.assertEqual(tab.pause_resume_btn.text(), "Pause")
        self.assertTrue(tab.pause_resume_btn.isEnabled())
        self.assertTrue(tab.abort_btn.isEnabled())
        self.assertFalse(tab.load_btn.isEnabled())
        # Paused: the same toggle shows Resume.
        tab._service._set_state(ExperimentState.PAUSED)
        self.assertEqual(tab.pause_resume_btn.text(), "Resume")
        self.assertTrue(tab.pause_resume_btn.isEnabled())

    def test_pause_resume_toggle_calls_service(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
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
        self.assertEqual(w.run_sequence_tab.state_label.text(), "loaded")
        # Fluid step list populated from the fluid protocol entries.
        self.assertGreater(w.run_sequence_tab.step_lists["fluid"].count(), 0)

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
        self.assertGreater(tab.step_lists["fluid"].count(), 0)
        # Selecting a step shows that entry's parameters in the table.
        tab.step_lists["fluid"].setCurrentRow(0)
        entry = tab._entries["fluid"][0]
        shown = self._table_dict(tab)
        self.assertEqual(shown.get("$type"), str(entry["$type"]))
        for key in entry:
            self.assertIn(key, shown)
        # The parameter box labels which list the step came from.
        self.assertIn("Fluid", tab.step_param_label.text())

    def test_close_event_is_safe(self):
        from PyQt6.QtGui import QCloseEvent

        w = self._build()
        # Should not raise even with no protocol / no hardware.
        w.closeEvent(QCloseEvent())

    def test_progress_bars_and_step_shading(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import (
            ExperimentTab,
            _FINISHED_COLOR,
            _ACTIVE_COLOR,
        )

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 10},
                    {"$type": "signal", "value": "x"},
                    {"$type": "incubate", "duration": 1},
                ]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "acquire", "frames": 1, "t_exp": 1},
                    {"$type": "acquire", "frames": 1, "t_exp": 1},
                ]
            },
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (1, 3),
            "img": (1, 2),
            "illu": (0, 0),
        }
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        tab._poll_progress()
        # overall: done 1+1+0=2 / total 3+2+0=5 = 40%, count shown on the right
        self.assertEqual(tab.overall_bar.value(), 40)
        self.assertEqual(tab.overall_count.text(), "2/5")
        # rounds: 2 acquires, img cur=1 -> imaging the 2nd round of 2, shown as
        # a "Round k/N" prefix on the status line (no separate rounds bar).
        self.assertFalse(hasattr(tab, "round_bar"))
        self.assertIn("Round 2/2", tab.step_status.text())
        # per-subsystem status: centered, current step name in brackets
        from PyQt6.QtCore import Qt

        # fluid cur=1 -> entries[1] is the 'signal' step, shown as 'sync'
        self.assertIn("fluid 1/3 (sync)", tab.step_status.text())
        self.assertTrue(
            bool(tab.step_status.alignment() & Qt.AlignmentFlag.AlignCenter)
        )
        # fluid rows: 0 finished, 1 active (== cur), 2 pending
        fluid = tab.step_lists["fluid"]
        self.assertEqual(fluid.item(0).background().color(), _FINISHED_COLOR)
        self.assertEqual(fluid.item(1).background().color(), _ACTIVE_COLOR)

    def test_round_status_shows_description(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        # Acquires carry the builder's human-readable round 'name'.
        svc.protocol = {
            "fluid": {"protocol_entries": []},
            "img": {
                "protocol_entries": [
                    {
                        "$type": "acquire",
                        "frames": 1,
                        "t_exp": 1,
                        "name": "R1",
                    },
                    {
                        "$type": "acquire",
                        "frames": 1,
                        "t_exp": 1,
                        "name": "A1 RESI round 2",
                    },
                ]
            },
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (0, 0),
            "img": (1, 2),
            "illu": (0, 0),
        }
        svc.step_progress.return_value = {}
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        tab._poll_progress()
        # img cur=1 -> on the 2nd acquire (round 2 of 2), labelled by name.
        self.assertIn("Round 2/2: A1 RESI round 2", tab.step_status.text())

    def test_current_round_progress_bar(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        # Two rounds: each fluid round ends at a wait-for-img; each img round
        # ends at an acquire.
        svc.protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 10},
                    {"$type": "signal", "value": "done flushing r0"},
                    {
                        "$type": "wait for signal",
                        "target": "img",
                        "value": "a",
                    },
                    {"$type": "inject", "reservoir_id": 2, "volume": 10},
                    {"$type": "signal", "value": "done flushing r1"},
                    {
                        "$type": "wait for signal",
                        "target": "img",
                        "value": "b",
                    },
                ]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "acquire", "frames": 1, "t_exp": 1},
                    {"$type": "signal", "value": "done imaging r0"},
                    {"$type": "acquire", "frames": 1, "t_exp": 1},
                    {"$type": "signal", "value": "done imaging r1"},
                ]
            },
            "illu": {"protocol_entries": []},
        }
        # Mid round 0: fluid on step 1 of {0,1,2}, img acquire not yet done.
        svc.progress.return_value = {
            "fluid": (1, 6),
            "img": (0, 4),
            "illu": (0, 0),
        }
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        tab._poll_progress()
        # Round-0 steps: fluid 0,1,2 + img 0 = 4 total; done = fluid step 0.
        self.assertEqual(tab.current_round_bar.value(), 25)
        self.assertEqual(tab.current_round_count.text(), "1/4")

    def test_duration_estimate_display(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        # fluid: 60 + 60 = 120 s; img: 1000 * 120 ms = 120 s; total 240 s = 4m.
        svc.protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "incubate", "duration": 60},
                    {"$type": "incubate", "duration": 60},
                ]
            },
            "img": {
                # Zero the calibrated acquire overheads so this stays a test
                # of the incubate+acquire arithmetic, not the tuned constants.
                "parameters": {
                    "est_frame_overhead": 0,
                    "est_acquire_setup": 0,
                },
                "protocol_entries": [
                    {"$type": "acquire", "frames": 1000, "t_exp": 120}
                ]
            },
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (0, 2),
            "img": (0, 1),
            "illu": (0, 0),
        }
        svc.step_progress.return_value = {}
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        # Total shown up front (before/at the start of the run).
        self.assertIn(
            "Estimated sequence duration: ~4m", tab.total_estimate_label.text()
        )
        # Once running, the single time line shows elapsed / remaining / total.
        tab._on_state_changed(ExperimentState.LOADED, ExperimentState.RUNNING)
        tab._poll_progress()
        self.assertIn("elapsed", tab.total_estimate_label.text())
        self.assertIn("~4m left", tab.total_estimate_label.text())
        # After the first fluid incubate, remaining drops to 60 + 120 = 3m.
        svc.progress.return_value = {
            "fluid": (1, 2),
            "img": (0, 1),
            "illu": (0, 0),
        }
        tab._poll_progress()
        self.assertIn("~3m left", tab.total_estimate_label.text())
        # The two progress bars stay horizontally aligned (same grid column).
        tab.resize(900, 700)
        tab.show()
        self.app.processEvents()
        geoms = [tab.overall_bar.geometry(), tab.current_round_bar.geometry()]
        self.assertEqual(len({g.x() for g in geoms}), 1)
        self.assertEqual(len({g.width() for g in geoms}), 1)

    def test_elapsed_time_shown_and_frozen_on_pause(self):
        import time
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            "fluid": {
                "protocol_entries": [{"$type": "incubate", "duration": 60}]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "acquire", "frames": 1000, "t_exp": 120}
                ]
            },
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (0, 1),
            "img": (0, 1),
            "illu": (0, 0),
        }
        svc.step_progress.return_value = {}
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        # Entering RUNNING starts the stopwatches; the time line then shows
        # both the overall and the current-round elapsed readings.
        tab._on_state_changed(ExperimentState.LOADED, ExperimentState.RUNNING)
        time.sleep(0.02)
        tab._poll_progress()
        self.assertIn("Overall:", tab.total_estimate_label.text())
        self.assertIn("Round:", tab.total_estimate_label.text())
        self.assertEqual(tab.total_estimate_label.text().count("elapsed"), 2)
        # Pausing freezes the elapsed reading.
        tab._on_state_changed(ExperimentState.RUNNING, ExperimentState.PAUSED)
        frozen = tab._overall_sw.elapsed()
        time.sleep(0.02)
        self.assertAlmostEqual(frozen, tab._overall_sw.elapsed(), places=2)

    def test_poll_ends_run_when_finished(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            "fluid": {"protocol_entries": [{"$type": "incubate"}]},
            "img": {"protocol_entries": []},
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (1, 1),
            "img": (0, 0),
            "illu": (0, 0),
        }
        svc.step_progress.return_value = {}
        svc.is_finished.return_value = True
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        # Polling notices completion and ends the run (-> FINISHED).
        tab._poll_progress()
        svc.end.assert_called_once()
        # While still running but not finished, it does not end.
        svc.end.reset_mock()
        svc.is_finished.return_value = False
        tab._poll_progress()
        svc.end.assert_not_called()

    def test_controls_reset_after_run_finishes(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.FINISHED
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._refresh_controls(ExperimentState.FINISHED)
        # Back to a "not running" state: Start available, Pause/Abort off.
        self.assertTrue(tab.start_btn.isEnabled())
        self.assertFalse(tab.pause_resume_btn.isEnabled())
        self.assertFalse(tab.abort_btn.isEnabled())

    def test_clear_button_enabled_only_when_loaded_not_running(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.IDLE
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        for state, enabled in (
            (ExperimentState.IDLE, False),
            (ExperimentState.LOADED, True),
            (ExperimentState.RUNNING, False),
            (ExperimentState.FINISHED, True),
            (ExperimentState.ABORTED, True),
        ):
            tab._refresh_controls(state)
            self.assertEqual(tab.clear_btn.isEnabled(), enabled, state)

    def test_clear_run_sequence_empties_view(self):
        from unittest.mock import MagicMock, patch
        from PyQt6.QtWidgets import QMessageBox
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.LOADED
        svc.protocol = {
            "fluid": {
                "protocol_entries": [{"$type": "incubate", "duration": 1}]
            },
            "img": {"protocol_entries": []},
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (1, 1),
            "img": (0, 0),
            "illu": (0, 0),
        }
        svc.step_progress.return_value = {}
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        tab._poll_progress()
        self.assertGreater(tab.step_lists["fluid"].count(), 0)
        # Confirming the dialog clears the run sequence via the service.
        with patch(
            "PycroFlow.gui.tabs.experiment_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            tab._on_clear()
        svc.clear_protocol.assert_called_once()
        # The bridge is mocked here, so drive the resulting IDLE transition
        # ourselves: with no orchestrator, progress() reports nothing and the
        # view empties / the bars reset.
        svc.protocol = None
        svc.progress.return_value = {}
        svc.state = ExperimentState.IDLE
        tab._on_state_changed(ExperimentState.LOADED, ExperimentState.IDLE)
        self.assertEqual(tab.step_lists["fluid"].count(), 0)
        self.assertEqual(tab.overall_count.text(), "0/0")
        self.assertEqual(tab.step_status.text(), "—")

    def test_clear_run_sequence_cancelled_keeps_it(self):
        from unittest.mock import MagicMock, patch
        from PyQt6.QtWidgets import QMessageBox
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.LOADED
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        with patch(
            "PycroFlow.gui.tabs.experiment_tab.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            tab._on_clear()
        svc.clear_protocol.assert_not_called()

    def test_within_step_bars(self):
        from unittest.mock import MagicMock
        from PycroFlow.services import ExperimentState
        from PycroFlow.gui.tabs.experiment_tab import ExperimentTab

        svc = MagicMock(name="service")
        svc.state = ExperimentState.RUNNING
        svc.protocol = {
            "fluid": {"protocol_entries": [{"$type": "incubate"}]},
            "img": {"protocol_entries": [{"$type": "acquire"}]},
            "illu": {"protocol_entries": []},
        }
        svc.progress.return_value = {
            "fluid": (0, 1),
            "img": (0, 1),
            "illu": (0, 0),
        }
        # Imaging mid-acquisition; fluid incubating; illu nothing.
        svc.step_progress.return_value = {
            "img": (200, 500, "frames"),
            "fluid": (12.0, 30.0, "incubate"),
            "illu": None,
        }
        tab = ExperimentTab(svc, MagicMock(name="bridge"))
        tab._populate_steps()
        tab._poll_progress()
        img_bar, img_count = tab.substep_bars["img"][1:]
        # isHidden() reflects the explicit show/hide flag (isVisible() needs a
        # shown top-level window, which offscreen tests don't have).
        self.assertFalse(img_bar.isHidden())
        self.assertEqual(img_bar.value(), 40)
        self.assertEqual(img_count.text(), "frames 200/500")
        fluid_count = tab.substep_bars["fluid"][2]
        self.assertEqual(fluid_count.text(), "incubate 12/30 s")
        # Illumination has no sub-progress -> its row stays hidden.
        self.assertTrue(tab.substep_bars["illu"][1].isHidden())

    @staticmethod
    def _sync_protocol():
        # One round: fluid flushes then signals; img waits, acquires, signals;
        # fluid then waits for imaging.
        return {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 10},
                    {"$type": "signal", "value": "flush0"},
                    {
                        "$type": "wait for signal",
                        "target": "img",
                        "value": "img0",
                    },
                    {"$type": "inject", "reservoir_id": 2, "volume": 10},
                ]
            },
            "img": {
                "protocol_entries": [
                    {
                        "$type": "wait for signal",
                        "target": "fluid",
                        "value": "flush0",
                    },
                    {"$type": "acquire", "frames": 1, "t_exp": 1},
                    {"$type": "signal", "value": "img0"},
                ]
            },
            "illu": {"protocol_entries": []},
        }

    def test_step_correlation_across_systems(self):
        w = self._build()
        tab = w.run_sequence_tab
        tab._service.load_protocol(self._sync_protocol())
        # Click the fluid round-0 inject -> img is parked at its wait.
        tab.step_lists["fluid"].setCurrentRow(0)
        self.assertEqual(tab.step_lists["img"].currentRow(), 0)
        # Click the img acquire -> fluid is blocked at its wait-for-imaging.
        tab.step_lists["img"].setCurrentRow(1)
        self.assertEqual(tab.step_lists["fluid"].currentRow(), 2)
        # The parameter box still reflects the clicked (img) step.
        self.assertIn("Imaging", tab.step_param_label.text())

    def test_center_button_enabled_only_while_running(self):
        from PycroFlow.services import ExperimentState

        w = self._build()
        tab = w.run_sequence_tab
        tab._service.load_protocol(self._sync_protocol())
        self.assertFalse(tab.center_btn.isEnabled())  # loaded, not running
        tab._service._set_state(ExperimentState.RUNNING)
        self.assertTrue(tab.center_btn.isEnabled())
        # Centring must not raise (scrolls each list to its current step).
        tab._center_on_current()

    def test_experiment_tab_lists_all_subsystem_steps(self):
        w = self._build()
        proto = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 100}
                ]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "acquire", "frames": 10, "t_exp": 100}
                ]
            },
            "illu": {
                "protocol_entries": [
                    {"$type": "set power", "laser": 560, "power": 30}
                ]
            },
        }
        tab = w.run_sequence_tab
        tab._service.load_protocol(proto)
        # Each subsystem has its own list.
        self.assertEqual(
            [tab.step_lists["fluid"].item(0).text()], ["0: inject"]
        )
        self.assertEqual(
            [tab.step_lists["img"].item(0).text()], ["0: acquire"]
        )
        self.assertEqual(
            [tab.step_lists["illu"].item(0).text()], ["0: set power"]
        )
        # Selecting the img step shows its parameters, labelled "Imaging".
        tab.step_lists["img"].setCurrentRow(0)
        shown = self._table_dict(tab)
        self.assertEqual(shown["$type"], "acquire")
        self.assertEqual(shown["frames"], "10")
        self.assertEqual(shown["t_exp"], "100")
        self.assertIn("Imaging", tab.step_param_label.text())
        # With no signals the lone steps are concurrent, so clicking img
        # correlates the other lists to their step 0.
        self.assertEqual(tab.step_lists["fluid"].currentRow(), 0)
        self.assertEqual(tab.step_lists["illu"].currentRow(), 0)

    def _load_inject(self, tab):
        proto = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 100}
                ]
            }
        }
        tab._service.load_protocol(proto)
        tab.step_lists["fluid"].setCurrentRow(0)

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
        self._set_cell(tab, "volume", "250")
        tab._on_apply()
        # _entries['fluid'][0] is a reference into the loaded protocol, so both
        # the cached entry and the service's protocol are updated, as int.
        self.assertEqual(tab._entries["fluid"][0]["volume"], 250)
        stored = tab._service.protocol["fluid"]["protocol_entries"][0]
        self.assertEqual(stored["volume"], 250)
        self.assertIsInstance(stored["volume"], int)

    def test_experiment_tab_invalid_edit_reported_and_skipped(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import experiment_tab as et

        w = self._build()
        tab = w.run_sequence_tab
        self._load_inject(tab)
        self._set_cell(tab, "volume", "not-a-number")
        with patch.object(et.QMessageBox, "warning") as warn:
            tab._on_apply()
        warn.assert_called_once()
        self.assertEqual(tab._entries["fluid"][0]["volume"], 100)

    def test_experiment_tab_type_field_not_editable(self):
        from PyQt6.QtCore import Qt

        w = self._build()
        tab = w.run_sequence_tab
        self._load_inject(tab)
        for r in range(tab.step_table.rowCount()):
            if tab.step_table.item(r, 0).text() == "$type":
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
        sys.modules.pop("monet", None)
        sys.modules.pop("monet.gui", None)
        # Restore the shared hardware mocks so later tests that import
        # PycroFlow.illumination / monet still find a mocked monet (these
        # tests pop it to exercise the absent / present paths).
        from PycroFlow.tests._mock_hardware import install_hardware_mocks

        install_hardware_mocks()

    def test_placeholder_when_monet_absent(self):
        sys.modules.pop("monet", None)
        sys.modules.pop("monet.gui", None)
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
        fake_monet = types.ModuleType("monet")
        fake_gui = types.ModuleType("monet.gui")

        class FakeMonetWindow(QWidget):
            def __init__(self):
                super().__init__()
                self.closed = False

            def close(self):
                self.closed = True
                return super().close()

        fake_gui.MonetMainWindow = FakeMonetWindow
        fake_monet.gui = fake_gui
        sys.modules["monet"] = fake_monet
        sys.modules["monet.gui"] = fake_gui

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
            w._system_service.get_monet_setup(), w.setup_combo.currentText()
        )

    def test_autoconnect_on_design_load(self):
        import PycroFlow

        w = self._win()
        w._on_setup_changed("Emulator")
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__),
            "examples",
            "sph_resi_6plex.yaml",
        )
        w.design_tab.load_design_path(path)
        self.assertEqual(
            w._system_service.connection_states(),
            {"fluid": True, "imaging": True, "illumination": True},
        )
        self.assertEqual(w.fluid_tab.status_label.text(), "connected")
        self.assertEqual(w.imaging_tab.status_label.text(), "connected")
        self.assertIsNotNone(w._experiment_service._fluid_system)

    def test_status_bar_confirms_connections(self):
        import PycroFlow

        w = self._win()
        w._on_setup_changed("Emulator")
        # Before connecting: setup shown, systems not connected.
        text = w.status_label.text()
        self.assertIn("Setup: Emulator", text)
        self.assertIn("not connected", text)
        # After autoconnect (via design load): each system confirmed.
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__),
            "examples",
            "sph_resi_6plex.yaml",
        )
        w.design_tab.load_design_path(path)
        text = w.status_label.text()
        self.assertIn("Fluid: ✓ connected", text)
        self.assertIn("Imaging: ✓ connected", text)
        self.assertIn("Illumination: ✓ connected", text)
        self.assertNotIn("not connected", text)

    def test_fluid_connect_requires_design(self):
        from unittest.mock import patch
        from PycroFlow.gui import main_window as mw

        w = self._win()
        with patch.object(mw.QMessageBox, "warning") as warn:
            w.fluid_tab._on_connect_clicked()
        warn.assert_called_once()
        self.assertIsNone(w._system_service.fluid_system)

    def test_manual_imaging_connect(self):
        w = self._win()
        w._on_setup_changed("Emulator")
        w.imaging_tab._on_connect_clicked()
        self.assertIsNotNone(w._system_service.imaging_system)
        self.assertEqual(w.imaging_tab.status_label.text(), "connected")

    def test_toolbar_connect_reconnects_when_already_connected(self):
        from unittest.mock import patch
        import PycroFlow

        w = self._win()
        w._on_setup_changed("Emulator")
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__),
            "examples",
            "sph_resi_6plex.yaml",
        )
        w.design_tab.load_design_path(path)  # autoconnects all subsystems
        self.assertEqual(
            w._system_service.connection_states(),
            {"fluid": True, "imaging": True, "illumination": True},
        )
        # _autoconnect skips already-connected subsystems (no-op here)...
        with patch.object(
            w, "_connect_system", wraps=w._connect_system
        ) as auto:
            w._autoconnect()
        auto.assert_not_called()
        # ...but the toolbar Connect re-targets every subsystem regardless, so
        # picking a different setup and hitting Connect actually reconnects.
        with patch.object(
            w, "_connect_system", wraps=w._connect_system
        ) as manual:
            w.act_connect.trigger()
        self.assertEqual(
            {c.args[0] for c in manual.call_args_list},
            {"fluid", "imaging", "illumination"},
        )

    def test_toolbar_connect_warns_without_setup(self):
        from unittest.mock import patch
        from PycroFlow.gui import main_window as mw

        w = self._win()
        w._system_service._setup = None  # simulate no setup loaded
        with patch.object(mw.QMessageBox, "warning") as warn:
            w.act_connect.trigger()
        warn.assert_called_once()

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
        self.assertFalse(w.act_disconnect.isEnabled())
        # STOP stays available during a run.
        self.assertTrue(w.fluid_tab.stop_btn.isEnabled())

        # Leaving the run unlocks everything again.
        w._experiment_service._set_state(ExperimentState.ABORTED)
        self.assertTrue(w.fluid_tab.connect_btn.isEnabled())
        self.assertTrue(w.monet_tab._embed_container.isEnabled())
        self.assertTrue(w.setup_combo.isEnabled())
        self.assertTrue(w.act_disconnect.isEnabled())

    def test_toolbar_disconnect_releases_all_systems(self):
        import PycroFlow

        w = self._win()
        w._on_setup_changed("Emulator")
        path = os.path.join(
            os.path.dirname(PycroFlow.__file__),
            "examples",
            "sph_resi_6plex.yaml",
        )
        w.design_tab.load_design_path(path)
        self.assertTrue(all(w._system_service.connection_states().values()))
        w.act_disconnect.trigger()
        self.assertEqual(
            w._system_service.connection_states(),
            {"fluid": False, "imaging": False, "illumination": False},
        )
        self.assertIn("not connected", w.status_label.text())

    def test_setup_change_disconnects_systems(self):
        from unittest.mock import patch

        w = self._win()
        w._on_setup_changed("Emulator")
        w.imaging_tab._on_connect_clicked()  # connect (no design needed)
        self.assertIsNotNone(w._system_service.imaging_system)
        # Changing the setup disconnects existing systems first, so the live
        # hardware never disagrees with the selected setup. With no design
        # loaded there is nothing to reconnect, so it stays disconnected.
        with patch.object(
            w._system_service,
            "disconnect_all",
            wraps=w._system_service.disconnect_all,
        ) as da:
            w._on_setup_changed("Emulator")
        da.assert_called_once()
        self.assertEqual(
            w._system_service.connection_states(),
            {"fluid": False, "imaging": False, "illumination": False},
        )

    def test_connect_disconnects_first(self):
        from unittest.mock import patch

        w = self._win()
        w._on_setup_changed("Emulator")
        w.imaging_tab._on_connect_clicked()  # initial connect
        self.assertIsNotNone(w._system_service.imaging_system)
        # Reconnecting frees the existing handle first.
        with patch.object(
            w._system_service, "disconnect", wraps=w._system_service.disconnect
        ) as dc:
            w.imaging_tab._on_connect_clicked()
        dc.assert_any_call("imaging")
        self.assertIsNotNone(w._system_service.imaging_system)

    def test_finished_run_unlocks_hardware(self):
        from PycroFlow.services import ExperimentState

        w = self._win()
        w._experiment_service._set_state(ExperimentState.RUNNING)
        self.assertFalse(w.setup_combo.isEnabled())
        # Completing the run re-enables the fluid / imaging / monet tabs and
        # the toolbar, like an abort does.
        w._experiment_service._set_state(ExperimentState.FINISHED)
        self.assertTrue(w.fluid_tab.connect_btn.isEnabled())
        self.assertTrue(w.imaging_tab.connect_btn.isEnabled())
        self.assertTrue(w.monet_tab._embed_container.isEnabled())
        self.assertTrue(w.setup_combo.isEnabled())
        self.assertTrue(w.act_connect.isEnabled())


def _example_design():
    import PycroFlow
    from PycroFlow.services import ExperimentService
    from PycroFlow.tests import chdir_to_test_output
    path = os.path.join(
        os.path.dirname(PycroFlow.__file__), 'examples', 'sph_resi_6plex.yaml')
    try:
        return ExperimentService().load_experiment_design(path), path
    finally:
        # The load chdirs to the design's folder; keep test output out of
        # the checkout.
        chdir_to_test_output()


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
        self.assertEqual(model.fluid.settings.experiment.type, "SPH-RESI")
        d = form.to_dict()
        tr = d["fluid"]["settings"]["experiment"]["target-rounds"]["A1"]
        self.assertEqual(len(tr["RESI-rounds"]), 6)

    def test_list_model_editor_add_remove(self):
        from PycroFlow.gui.widgets.schema_form import _ListModelEditor
        from PycroFlow.schemas.experiment_design import ResiRound

        ed = _ListModelEditor(
            ResiRound, [{"adapter": "a", "adapter_incubation": 1}], "RESI"
        )
        self.assertEqual(len(ed.get_value()), 1)
        ed._add_item({"adapter": "b", "adapter_incubation": 2})
        self.assertEqual(len(ed.get_value()), 2)
        ed._remove(ed._items[0])
        self.assertEqual(len(ed.get_value()), 1)
        self.assertEqual(ed.get_value()[0]["adapter"], "b")

    def test_scalar_defaults_seeded(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidParameters

        form = SchemaForm(FluidParameters, {})
        self.assertEqual(form.to_dict()["mode"], "tubing_ignore")

    def test_form_labels_are_left_aligned(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QFormLayout
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidParameters

        form = SchemaForm(FluidParameters, {})
        lay = form.layout()
        self.assertIsInstance(lay, QFormLayout)
        self.assertTrue(
            bool(lay.labelAlignment() & Qt.AlignmentFlag.AlignLeft)
        )

    def test_mode_is_dropdown(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm, _ChoiceEditor
        from PycroFlow.schemas.experiment_design import FluidParameters

        form = SchemaForm(FluidParameters, {})
        ed = form.field_editor("mode")
        self.assertIsInstance(ed, _ChoiceEditor)
        items = [ed._combo.itemText(i) for i in range(ed._combo.count())]
        self.assertEqual(set(items), {"tubing_ignore", "tubing_stack"})
        self.assertEqual(ed.get_value(), "tubing_ignore")

    def test_imager_fields_are_name_dropdowns(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm, _ChoiceEditor
        from PycroFlow.schemas.experiment_design import SphResiExperiment

        form = SchemaForm(
            SphResiExperiment,
            {
                "type": "SPH-RESI",
                "wash_buffer_1": "R1",
                "blocker": "R2",
                "blocker_incubation": 5,
                "round0": None,
                "target-rounds": {},
            },
            context={"reservoir_names": ["R1", "R2", "C+"]},
            skip_fields={"type"},
        )
        ed = form.field_editor("wash_buffer_1")
        self.assertIsInstance(ed, _ChoiceEditor)
        items = [ed._combo.itemText(i) for i in range(ed._combo.count())]
        self.assertIn("R1", items)
        self.assertIn("C+", items)
        self.assertIn("", items)  # the None option

    def test_laser_is_dropdown_from_monet_lasers(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm, _ChoiceEditor
        from PycroFlow.schemas.experiment_design import IlluSettings

        form = SchemaForm(
            IlluSettings,
            {"laser": 642, "power_acq": 70},
            context={"lasers": [488, 561, 640, 642]},
        )
        ed = form.field_editor("laser")
        self.assertIsInstance(ed, _ChoiceEditor)
        items = [ed._combo.itemText(i) for i in range(ed._combo.count())]
        self.assertEqual(items, ["488", "561", "640", "642"])
        # The selected laser round-trips back to an int.
        self.assertEqual(form.to_dict()["laser"], 642)
        self.assertIsInstance(form.to_dict()["laser"], int)

    def test_laser_stays_typeable_without_monet_lasers(self):
        # A monet config that declares no lasers (or no setup loaded) must
        # not lock the field: the dropdown is editable, so any wavelength
        # can still be entered.
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import IlluSettings
        form = SchemaForm(IlluSettings, {'laser': 642, 'power_acq': 70},
                          context={'lasers': []})
        ed = form.field_editor('laser')
        self.assertTrue(ed._combo.isEditable())
        ed._combo.setCurrentText('750')
        self.assertEqual(form.to_dict()['laser'], 750)

    def test_reservoir_ids_become_dropdown_when_setup_arrives(self):
        # The form is built before a setup is loaded (empty startup form), so
        # the id column must follow the context rather than snapshot it.
        from PycroFlow.gui.widgets.schema_form import SchemaForm, FormContext
        from PycroFlow.schemas.experiment_design import FluidSettings
        from PyQt6.QtWidgets import QComboBox, QLineEdit
        ctx = FormContext({'reservoir_ids': []})
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'imager1', 2: 'imager2'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'imager1'}},
            context=ctx)
        ed = form.field_editor('reservoir_names')
        self.assertIsInstance(ed._rows[0][0], QLineEdit)
        ctx.set_options('reservoir_ids', [1, 2, 3])
        id_cell = ed._rows[0][0]
        self.assertIsInstance(id_cell, QComboBox)
        self.assertEqual(
            [id_cell.itemText(i) for i in range(id_cell.count())],
            ['1', '2', '3'])
        # Switching the column to dropdowns must not lose the entered data.
        self.assertEqual(ed.get_value(), {1: 'imager1', 2: 'imager2'})

    @staticmethod
    def _next_tab_stop(w):
        """The widget Tab would move focus to (Qt skips non-tabbable ones)."""
        from PyQt6.QtCore import Qt
        cur = w.nextInFocusChain()
        while cur is not w:
            if cur.focusPolicy() & Qt.FocusPolicy.TabFocus:
                return cur
            cur = cur.nextInFocusChain()
        return None

    def test_tab_runs_down_the_name_column(self):
        # Tabbing out of a name field reached the row's ✕ button; it should
        # go to the next row's name, so names are filled straight down. The
        # id dropdowns are picked from their list, not tabbed through.
        from PyQt6.QtCore import Qt
        from PycroFlow.gui.widgets.schema_form import SchemaForm, FormContext
        from PycroFlow.schemas.experiment_design import FluidSettings
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'a', 2: 'b', 3: 'c'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'a'}},
            context=FormContext({'reservoir_ids': [1, 2, 3]}))
        ed = form.field_editor('reservoir_names')
        (r0_id, r0_name, r0_rm), (_, r1_name, _), (_, r2_name, _) = ed._rows
        self.assertEqual(r0_rm.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(r0_id.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        self.assertIs(self._next_tab_stop(r0_name), r1_name)
        self.assertIs(self._next_tab_stop(r1_name), r2_name)

    def test_free_text_id_column_stays_tabbable(self):
        # Without a setup the id column is a text field, not a dropdown — it
        # must stay in the tab chain or it could not be filled in at all.
        from PyQt6.QtCore import Qt
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'a', 2: 'b'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'a'}})
        ed = form.field_editor('reservoir_names')
        (r0_id, r0_name, _), (r1_id, _, _) = ed._rows
        self.assertNotEqual(r0_id.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        self.assertIs(self._next_tab_stop(r0_id), r0_name)
        self.assertIs(self._next_tab_stop(r0_name), r1_id)

    def test_setup_options_refresh_into_live_form(self):
        # Picking/switching a setup after a design is loaded must refresh the
        # setup-derived dropdowns in place, not leave them stale.
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab)
        lasers = []
        tab = ExperimentDesignTab(
            ExperimentService(), laser_options_provider=lambda: list(lasers),
            reservoir_ids_provider=lambda: [1, 2])
        tab._set_form({'base_name': 'x',
                       'illu': {'settings': {'laser': 642, 'power_acq': 70}}})
        illu = tab._form.field_editor('illu')
        ed = illu._form.field_editor('settings')._form.field_editor('laser')
        self.assertEqual(
            [ed._combo.itemText(i) for i in range(ed._combo.count())], ['642'])
        lasers[:] = [488, 561, 640]
        tab.refresh_setup_options()
        self.assertEqual(
            [ed._combo.itemText(i) for i in range(ed._combo.count())],
            ['488', '561', '640', '642'])

    def test_laser_stays_typeable_without_monet_lasers(self):
        # A monet config that declares no lasers (or no setup loaded) must
        # not lock the field: the dropdown is editable, so any wavelength
        # can still be entered.
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import IlluSettings
        form = SchemaForm(IlluSettings, {'laser': 642, 'power_acq': 70},
                          context={'lasers': []})
        ed = form.field_editor('laser')
        self.assertTrue(ed._combo.isEditable())
        ed._combo.setCurrentText('750')
        self.assertEqual(form.to_dict()['laser'], 750)

    def test_reservoir_ids_become_dropdown_when_setup_arrives(self):
        # The form is built before a setup is loaded (empty startup form), so
        # the id column must follow the context rather than snapshot it.
        from PycroFlow.gui.widgets.schema_form import SchemaForm, FormContext
        from PycroFlow.schemas.experiment_design import FluidSettings
        from PyQt6.QtWidgets import QComboBox, QLineEdit
        ctx = FormContext({'reservoir_ids': []})
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'imager1', 2: 'imager2'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'imager1'}},
            context=ctx)
        ed = form.field_editor('reservoir_names')
        self.assertIsInstance(ed._rows[0][0], QLineEdit)
        ctx.set_options('reservoir_ids', [1, 2, 3])
        id_cell = ed._rows[0][0]
        self.assertIsInstance(id_cell, QComboBox)
        self.assertEqual(
            [id_cell.itemText(i) for i in range(id_cell.count())],
            ['1', '2', '3'])
        # Switching the column to dropdowns must not lose the entered data.
        self.assertEqual(ed.get_value(), {1: 'imager1', 2: 'imager2'})

    @staticmethod
    def _next_tab_stop(w):
        """The widget Tab would move focus to (Qt skips non-tabbable ones)."""
        from PyQt6.QtCore import Qt
        cur = w.nextInFocusChain()
        while cur is not w:
            if cur.focusPolicy() & Qt.FocusPolicy.TabFocus:
                return cur
            cur = cur.nextInFocusChain()
        return None

    def test_tab_runs_down_the_name_column(self):
        # Tabbing out of a name field reached the row's ✕ button; it should
        # go to the next row's name, so names are filled straight down. The
        # id dropdowns are picked from their list, not tabbed through.
        from PyQt6.QtCore import Qt
        from PycroFlow.gui.widgets.schema_form import SchemaForm, FormContext
        from PycroFlow.schemas.experiment_design import FluidSettings
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'a', 2: 'b', 3: 'c'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'a'}},
            context=FormContext({'reservoir_ids': [1, 2, 3]}))
        ed = form.field_editor('reservoir_names')
        (r0_id, r0_name, r0_rm), (_, r1_name, _), (_, r2_name, _) = ed._rows
        self.assertEqual(r0_rm.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(r0_id.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        self.assertIs(self._next_tab_stop(r0_name), r1_name)
        self.assertIs(self._next_tab_stop(r1_name), r2_name)

    def test_free_text_id_column_stays_tabbable(self):
        # Without a setup the id column is a text field, not a dropdown — it
        # must stay in the tab chain or it could not be filled in at all.
        from PyQt6.QtCore import Qt
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings
        form = SchemaForm(FluidSettings, {
            'vol_wash': 10, 'reservoir_names': {1: 'a', 2: 'b'},
            'experiment': {'type': 'Exchange', 'wash_buffer': 'a'}})
        ed = form.field_editor('reservoir_names')
        (r0_id, r0_name, _), (r1_id, _, _) = ed._rows
        self.assertNotEqual(r0_id.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        self.assertIs(self._next_tab_stop(r0_id), r0_name)
        self.assertIs(self._next_tab_stop(r0_name), r1_id)

    def test_setup_options_refresh_into_live_form(self):
        # Picking/switching a setup after a design is loaded must refresh the
        # setup-derived dropdowns in place, not leave them stale.
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab)
        lasers = []
        tab = ExperimentDesignTab(
            ExperimentService(), laser_options_provider=lambda: list(lasers),
            reservoir_ids_provider=lambda: [1, 2])
        tab._set_form({'base_name': 'x',
                       'illu': {'settings': {'laser': 642, 'power_acq': 70}}})
        illu = tab._form.field_editor('illu')
        ed = illu._form.field_editor('settings')._form.field_editor('laser')
        self.assertEqual(
            [ed._combo.itemText(i) for i in range(ed._combo.count())], ['642'])
        lasers[:] = [488, 561, 640]
        tab.refresh_setup_options()
        self.assertEqual(
            [ed._combo.itemText(i) for i in range(ed._combo.count())],
            ['488', '561', '640', '642'])

    def test_imager_dropdowns_update_live_on_reservoir_edit(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        form = SchemaForm(
            FluidSettings,
            {
                "vol_wash": 10,
                "reservoir_names": {1: "R1", 2: "R2"},
                "experiment": {"type": "Exchange", "wash_buffer": "R1"},
            },
            context={
                "reservoir_names": ["R1", "R2"],
                "reservoir_ids": [1, 2, 3],
            },
        )
        wb = form.field_editor("experiment")._form.field_editor("wash_buffer")
        self.assertIn(
            "R2", [wb._combo.itemText(i) for i in range(wb._combo.count())]
        )
        # Rename reservoir 2 in the table -> the imager dropdown updates.
        name_cell = form.field_editor("reservoir_names")._rows[1][1]
        name_cell.setText("NEWDYE")
        items = [wb._combo.itemText(i) for i in range(wb._combo.count())]
        self.assertIn("NEWDYE", items)
        self.assertNotIn("R2", items)

    def test_exchange_imagers_are_addremove_dropdowns(self):
        from PycroFlow.gui.widgets.schema_form import (
            SchemaForm,
            _ListChoiceEditor,
            _ChoiceEditor,
        )
        from PycroFlow.schemas.experiment_design import ExchangeExperiment

        form = SchemaForm(
            ExchangeExperiment,
            {"type": "Exchange", "wash_buffer": "C+", "imagers": ["R1", "R2"]},
            context={"reservoir_names": ["R1", "R2", "R3", "C+"]},
            skip_fields={"type"},
        )
        ed = form.field_editor("imagers")
        self.assertIsInstance(ed, _ListChoiceEditor)
        self.assertEqual(len(ed._items), 2)
        # Box is titled 'rounds' with a per-row 'imager round {k}' label.
        self.assertEqual(ed.title(), "rounds")
        self.assertEqual(
            [lbl.text() for _, _, lbl in ed._items],
            ["imager round 1", "imager round 2"],
        )
        # Each round is a reservoir-name dropdown.
        row0 = ed._items[0][1]
        self.assertIsInstance(row0, _ChoiceEditor)
        self.assertIn(
            "R3", [row0._combo.itemText(i) for i in range(row0._combo.count())]
        )
        # Add / remove rows like the RESI rounds; labels renumber.
        ed._add_item("R3")
        self.assertEqual(form.to_dict()["imagers"], ["R1", "R2", "R3"])
        self.assertEqual(ed._items[-1][2].text(), "imager round 3")
        ed._remove(ed._items[0])
        self.assertEqual(form.to_dict()["imagers"], ["R2", "R3"])
        self.assertEqual(
            [lbl.text() for _, _, lbl in ed._items],
            ["imager round 1", "imager round 2"],
        )

    def test_exchange_field_order_initial_imager_before_rounds(self):
        from PycroFlow.schemas.experiment_design import ExchangeExperiment

        fields = [f for f in ExchangeExperiment.model_fields if f != "type"]
        self.assertEqual(fields, ["wash_buffer", "initial_imager", "imagers"])

    def test_exchange_imager_rows_update_live_on_reservoir_edit(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        data = {
            "vol_wash": 10,
            "reservoir_names": {1: "R1", 2: "R2"},
            "experiment": {
                "type": "Exchange",
                "wash_buffer": "R1",
                "imagers": ["R1"],
            },
        }
        form = SchemaForm(
            FluidSettings,
            data,
            context={
                "reservoir_names": ["R1", "R2"],
                "reservoir_ids": [1, 2, 3],
            },
        )
        imagers = form.field_editor("experiment")._form.field_editor("imagers")
        row0 = imagers._items[0][1]
        self.assertIn(
            "R2", [row0._combo.itemText(i) for i in range(row0._combo.count())]
        )
        # Rename reservoir 2 -> the imager dropdown options follow.
        form.field_editor("reservoir_names")._rows[1][1].setText("NEWDYE")
        self.assertIn(
            "NEWDYE",
            [row0._combo.itemText(i) for i in range(row0._combo.count())],
        )

    def test_experiment_type_not_duplicated(self):
        # The union selector supplies 'type'; the variant sub-form must not
        # render a separate 'type' editor, but to_dict still carries it.
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        form = SchemaForm(
            FluidSettings,
            {
                "vol_wash": 10,
                "reservoir_names": {1: "R1"},
                "experiment": {"type": "Exchange", "wash_buffer": "R1"},
            },
            context={"reservoir_names": ["R1"]},
        )
        union = form.field_editor("experiment")
        self.assertNotIn("type", union._form._editors)
        self.assertEqual(union.get_value()["type"], "Exchange")

    def test_special_names_id_first_and_roundtrips(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        form = SchemaForm(
            FluidSettings,
            {
                "vol_wash": 10,
                "reservoir_names": {1: "R1", 7: "C+"},
                "special_names": {"flushbuffer_a": 7},
                "experiment": {"type": "Exchange", "wash_buffer": "R1"},
            },
            context={"reservoir_names": ["R1", "C+"], "reservoir_ids": [1, 7]},
        )
        sn = form.field_editor("special_names")
        self.assertTrue(sn._dvf)  # the id (value) is shown first
        # Stored mapping is still name -> id.
        self.assertEqual(form.to_dict()["special_names"], {"flushbuffer_a": 7})

    def test_reservoir_id_dropdown_restricted_to_setup(self):
        from PyQt6.QtWidgets import QComboBox
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        form = SchemaForm(
            FluidSettings,
            {
                "vol_wash": 10,
                "reservoir_names": {1: "R1"},
                "experiment": {"type": "Exchange", "wash_buffer": "R1"},
            },
            context={"reservoir_names": ["R1"], "reservoir_ids": [1, 2, 3]},
        )
        rn = form.field_editor("reservoir_names")
        key_w = rn._rows[0][0]
        self.assertIsInstance(key_w, QComboBox)
        items = [key_w.itemText(i) for i in range(key_w.count())]
        self.assertEqual(set(items), {"1", "2", "3"})

    def test_cleaning_reservoirs_tooltip(self):
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidSettings

        form = SchemaForm(
            FluidSettings,
            {
                "vol_wash": 10,
                "reservoir_names": {1: "R1"},
                "experiment": {"type": "Exchange", "wash_buffer": "R1"},
            },
            context={"reservoir_names": ["R1"]},
        )
        self.assertIn(
            "omma", form.field_editor("cleaning_reservoirs").toolTip()
        )

    def test_units_shown_next_to_inputs(self):
        from PyQt6.QtWidgets import QLabel
        from PycroFlow.gui.widgets.schema_form import SchemaForm
        from PycroFlow.schemas.experiment_design import FluidParameters

        form = SchemaForm(FluidParameters, {})
        vel = form.field_editor("max_velocity")
        self.assertIn(
            "µl/min", [lbl.text() for lbl in vel.findChildren(QLabel)]
        )
        # A unitless field gets no unit label.
        ef = form.field_editor("extractionfactor")
        self.assertEqual([lbl.text() for lbl in ef.findChildren(QLabel)], [])


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestExperimentDesignTab(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        # Loading a design chdirs to its folder; keep test output out of the
        # checkout for whatever runs next.
        from PycroFlow.tests import chdir_to_test_output
        chdir_to_test_output()

    def test_load_and_translate(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        _, path = _example_design()
        svc = ExperimentService()
        translated = []
        tab = ExperimentDesignTab(
            svc, on_translated=lambda: translated.append(1)
        )
        tab.load_design_path(path)
        self.assertEqual(
            svc.experiment_design["fluid"]["settings"]["experiment"]["type"],
            "SPH-RESI",
        )
        tab._on_translate()
        self.assertEqual(svc.state.value, "loaded")
        self.assertEqual(translated, [1])

    def test_drag_drop_loads_design(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        _, path = _example_design()
        svc = ExperimentService()
        tab = ExperimentDesignTab(svc)
        tab.on_yaml_dropped(path)
        self.assertIsNotNone(svc.experiment_design)

    def test_reservoir_ids_provider_feeds_form(self):
        from PyQt6.QtWidgets import QComboBox
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        self.addCleanup(os.chdir, os.getcwd())  # load_design_path chdirs
        _, path = _example_design()
        tab = ExperimentDesignTab(
            ExperimentService(),
            reservoir_ids_provider=lambda: [1, 2, 3, 4, 5, 6, 7],
        )
        tab.load_design_path(path)
        settings = (
            tab._form.field_editor("fluid")
            ._form.field_editor("settings")
            ._form
        )
        rn = settings.field_editor("reservoir_names")
        key_w = rn._rows[0][0]
        self.assertIsInstance(key_w, QComboBox)

    def test_save_dir_absolute_path_hint(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        tab = ExperimentDesignTab(ExperimentService())
        editor = tab._form.field_editor("save_dir")
        line = editor.line_edit()
        # Relative path -> hint shows the resolved absolute destination.
        line.setText("subdir")
        self.assertEqual(
            tab._save_dir_hint.text(), "→ {}".format(os.path.abspath("subdir"))
        )
        # '.' resolves to the working directory.
        line.setText(".")
        self.assertEqual(
            tab._save_dir_hint.text(), "→ {}".format(os.path.abspath("."))
        )
        # Absolute path -> no hint shown.
        line.setText(os.path.abspath("subdir"))
        self.assertEqual(tab._save_dir_hint.text(), "")

    def test_duration_estimate_is_automatic_no_button(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        self.addCleanup(os.chdir, os.getcwd())  # load_design_path chdirs
        _, path = _example_design()
        tab = ExperimentDesignTab(ExperimentService())
        # The explicit button is gone — estimation is live.
        self.assertFalse(hasattr(tab, "estimate_btn"))
        tab.load_design_path(path)
        tab._recompute_estimate()  # fire the debounced recompute directly
        text = tab.estimate_label.text()
        self.assertIn("Estimated: ~", text)
        # Total reagent volume now rides alongside the duration.
        self.assertIn("reagents", text)
        # The folded preview lists the volumes and the event sequence.
        preview = tab.preview_text.toPlainText()
        self.assertIn("Volumes required:", preview)
        self.assertIn("Sequence of events:", preview)

    def test_incomplete_design_estimate_is_graceful(self):
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        # Empty default form cannot compile; the label says so, no exception.
        tab = ExperimentDesignTab(ExperimentService())
        tab._recompute_estimate()
        self.assertIn("incomplete", tab.estimate_label.text())

    def test_sections_are_collapsible_without_dropping_data(self):
        from PyQt6.QtCore import Qt
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )
        from PycroFlow.gui.widgets.schema_form import _ModelEditor

        self.addCleanup(os.chdir, os.getcwd())
        _, path = _example_design()
        tab = ExperimentDesignTab(ExperimentService())
        tab.load_design_path(path)
        sections = tab._form.findChildren(_ModelEditor)
        self.assertTrue(sections)
        # Each section has an arrow toggle, expanded (▾) by default.
        self.assertTrue(all(hasattr(s, "_toggle") for s in sections))
        fluid = tab._form.field_editor("fluid")
        self.assertEqual(fluid._toggle.arrowType(), Qt.ArrowType.DownArrow)
        # Collapsing flips the arrow and hides the body but keeps the value.
        # isVisibleTo() reflects the explicit hide without needing show().
        fluid._toggle.setChecked(False)
        self.assertEqual(fluid._toggle.arrowType(), Qt.ArrowType.RightArrow)
        self.assertFalse(fluid._form.isVisibleTo(fluid))
        self.assertIn("fluid", tab._form.to_dict())
        # Re-expanding restores the arrow and the body.
        fluid._toggle.setChecked(True)
        self.assertEqual(fluid._toggle.arrowType(), Qt.ArrowType.DownArrow)
        self.assertTrue(fluid._form.isVisibleTo(fluid))

    def test_clear_resets_design_and_form(self):
        from unittest.mock import patch
        from PycroFlow.services import ExperimentService
        from PycroFlow.gui.tabs import experiment_design_tab as edt
        from PycroFlow.gui.tabs.experiment_design_tab import (
            ExperimentDesignTab,
        )

        self.addCleanup(os.chdir, os.getcwd())
        _, path = _example_design()
        svc = ExperimentService()
        tab = ExperimentDesignTab(svc)
        tab.load_design_path(path)
        self.assertIsNotNone(svc.experiment_design)
        loaded_name = tab._form.to_dict().get("base_name")
        self.assertTrue(loaded_name)
        # Confirming clears the service design and rebuilds an empty form.
        with patch.object(
            edt.QMessageBox,
            "question",
            return_value=edt.QMessageBox.StandardButton.Yes,
        ):
            tab._on_clear()
        self.assertIsNone(svc.experiment_design)
        self.assertNotEqual(tab._form.to_dict().get("base_name"), loaded_name)


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMonetSetSetup(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        sys.modules.pop("monet", None)
        sys.modules.pop("monet.gui", None)
        from PycroFlow.tests._mock_hardware import install_hardware_mocks

        install_hardware_mocks()

    def test_set_setup_preselects_without_autoconnect(self):
        from PyQt6.QtWidgets import QWidget, QComboBox

        fake_monet = types.ModuleType("monet")
        fake_gui = types.ModuleType("monet.gui")

        class FakeMonetWidget(QWidget):
            def __init__(self, initial_microscope=None):
                super().__init__()
                self.initial_microscope = initial_microscope
                self._scope_combo = QComboBox()
                self._scope_combo.addItems(["Emulator", "Mercury"])

        fake_gui.MonetWidget = FakeMonetWidget
        fake_monet.gui = fake_gui
        sys.modules["monet"] = fake_monet
        sys.modules["monet.gui"] = fake_gui

        from PycroFlow.gui.tabs.monet_tab import MonetTab

        tab = MonetTab()
        tab.set_setup("Mercury")
        # No auto-connect: initial_microscope is NOT passed (avoids fighting
        # PycroFlow's IlluminationSystem for the laser COM port).
        self.assertIsNone(tab._monet_window.initial_microscope)
        # The scope is pre-selected for display.
        self.assertEqual(
            tab._monet_window._scope_combo.currentText(), "Mercury"
        )


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

    def _tab(self, reservoir_ids=(1, 3, 8, 20)):
        from unittest.mock import MagicMock
        from PycroFlow.gui.tabs.fluid_tab import FluidTab

        svc = MagicMock(name="system_service")
        svc.fluid_system = object()
        # The manual valve dropdown is filled from the setup's manifold.
        svc.reservoir_ids.return_value = list(reservoir_ids)
        return FluidTab(svc), svc

    def test_fill_calls_service(self):
        tab, svc = self._tab()
        tab._on_fill()
        svc.fill_tubings.assert_called_once()

    def test_clean_confirm_yes_calls_service(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft

        tab, svc = self._tab()
        with patch.object(
            ft.QMessageBox,
            "question",
            return_value=ft.QMessageBox.StandardButton.Yes,
        ):
            tab._on_clean()
        svc.clean_tubings.assert_called_once()

    def test_clean_confirm_no_does_nothing(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft

        tab, svc = self._tab()
        with patch.object(
            ft.QMessageBox,
            "question",
            return_value=ft.QMessageBox.StandardButton.No,
        ):
            tab._on_clean()
        svc.clean_tubings.assert_not_called()

    def test_stroke_calls_manual_pump(self):
        tab, svc = self._tab()
        tab.stroke_pump.setCurrentText("pump_a")
        tab.stroke_vol.setText("150")
        tab.stroke_vel.setText("200")
        tab.stroke_pickup.setCurrentText("in")
        tab.stroke_dispense.setCurrentText("out")
        tab._on_stroke()
        svc.manual_pump.assert_called_once_with(
            "pump_a",
            vol=150.0,
            pickup_dir="in",
            dispense_dir="out",
            velocity=200.0,
        )

    def test_move_includes_reservoirs(self):
        tab, svc = self._tab()
        tab.move_pump.setCurrentText("pump_a")
        tab.move_vol.setText("80")
        tab.move_pickup_res.setText("5")
        tab.move_dispense_res.setText("7")
        tab.move_pickup_dir.setCurrentText("in")
        tab.move_dispense_dir.setCurrentText("in")
        tab._on_move()
        svc.manual_pump.assert_called_once_with(
            "pump_a",
            vol=80.0,
            pickup_dir="in",
            dispense_dir="in",
            pickup_res=5,
            dispense_res=7,
        )

    def test_valve_dropdown_offers_the_setups_manifold(self):
        # Sparse manifolds (ibidi) are common: the ids offered are exactly
        # those the setup wires, not a 1..N range nor the design's subset.
        tab, _ = self._tab(reservoir_ids=(2, 3, 8, 20))
        self.assertEqual(
            [tab.valve_res.itemData(i) for i in range(tab.valve_res.count())],
            [2, 3, 8, 20])

    def test_valve_dropdown_offers_the_setups_manifold(self):
        # Sparse manifolds (ibidi) are common: the ids offered are exactly
        # those the setup wires, not a 1..N range nor the design's subset.
        tab, _ = self._tab(reservoir_ids=(2, 3, 8, 20))
        self.assertEqual(
            [tab.valve_res.itemData(i) for i in range(tab.valve_res.count())],
            [2, 3, 8, 20])

    def test_set_valves_calls_service(self):
        tab, svc = self._tab()
        tab.valve_res.setCurrentIndex(tab.valve_res.findData(8))
        tab._on_set_valves()
        svc.set_valves.assert_called_once_with(8)

    def test_close_all_valves_calls_service(self):
        tab, svc = self._tab()
        tab._on_close_valves()
        svc.close_all_valves.assert_called_once()

    def test_close_all_valves_button_only_for_multiplexer(self):
        # The ibidi-only control is shown for multiplexer setups and hidden
        # for Hamilton-rotary-valve ones.
        tab, svc = self._tab()
        svc.has_multiplexer.return_value = True
        tab._refresh_reservoirs()
        self.assertFalse(tab.close_valves_btn.isHidden())
        svc.has_multiplexer.return_value = False
        tab._refresh_reservoirs()
        self.assertTrue(tab.close_valves_btn.isHidden())

    def test_set_valves_without_wired_reservoirs_warns(self):
        from unittest.mock import patch
        from PycroFlow.gui.tabs import fluid_tab as ft
        tab, svc = self._tab(reservoir_ids=())
        self.assertFalse(tab.valve_btn.isEnabled())
        with patch.object(ft.QMessageBox, 'warning') as warn:
            tab._on_set_valves()
        warn.assert_called_once()
        svc.set_valves.assert_not_called()

    def test_route_hint_follows_the_selected_reservoir(self):
        tab, svc = self._tab(reservoir_ids=(2, 8))
        svc.describe_reservoir_route.side_effect = (
            lambda rid: 'route for {}'.format(rid))
        tab.valve_res.setCurrentIndex(tab.valve_res.findData(8))
        self.assertEqual(tab.valve_route.text(), 'route for 8')
        tab.valve_res.setCurrentIndex(tab.valve_res.findData(2))
        self.assertEqual(tab.valve_route.text(), 'route for 2')

    def test_route_hint_without_a_setup(self):
        tab, _ = self._tab(reservoir_ids=())
        self.assertIn('No reservoirs wired', tab.valve_route.text())

    def test_manual_controls_are_explained(self):
        # The two pump groups differ in whether they re-route the valves;
        # that difference must be stated, not left to be discovered.
        tab, _ = self._tab()
        from PyQt6.QtWidgets import QLabel
        hints = ' '.join(lbl.text() for lbl in tab.findChildren(QLabel))
        self.assertIn('does NOT change reservoir routing', hints)
        self.assertIn('Sets the valves itself', hints)
        self.assertIn('no liquid is moved', hints)
        self.assertTrue(tab.stroke_pump.toolTip())
        self.assertTrue(tab.move_pickup_res.toolTip())
        self.assertTrue(tab.valve_res.toolTip())

    def test_refresh_follows_a_setup_change(self):
        tab, svc = self._tab(reservoir_ids=(1, 2))
        svc.reservoir_ids.return_value = [5, 6, 7]
        tab.refresh()
        self.assertEqual(
            [tab.valve_res.itemData(i) for i in range(tab.valve_res.count())],
            [5, 6, 7])

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


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestFluidSchematic(unittest.TestCase):
    """The live fluid-wiring schematic renders topology + valve state."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_grid_layout_matches_hardware_numbering(self):
        # Ports numbered left-to-right, bottom-to-top: port 1 lower left,
        # port 6 lower right, port 7 directly above port 1.
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        cell = FluidSchematic._grid_cell
        self.assertEqual(cell(1, 6, 4), (0, 3))    # bottom-left
        self.assertEqual(cell(6, 6, 4), (5, 3))    # bottom-right
        self.assertEqual(cell(7, 6, 4), (0, 2))    # above port 1, row start
        self.assertEqual(cell(12, 6, 4), (5, 2))   # end of the second row

    def test_renders_topology_and_live_state_without_error(self):
        from PyQt6.QtGui import QPixmap
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Ibidi')
        widget = FluidSchematic()
        widget.resize(900, 500)
        widget.set_topology(svc.fluid_topology())
        opened = [c in (1, 6, 12, 8) for c in range(1, 25)]
        widget.set_state({
            'multiplexer': {'channels': 24, 'open': opened},
            'pump_a': {'valve': 'in', 'volume': 250.0, 'capacity': 500.0},
            'pump_out': {'valve': 'out', 'volume': 0.0, 'capacity': 5000.0},
        })
        pix = QPixmap(widget.size())
        widget.render(pix)   # exercises paintEvent; must not raise
        self.assertFalse(pix.isNull())
        # Robust to missing / partial data too.
        widget.set_topology(None)
        widget.render(pix)
        widget.set_topology({'multiplexer': None, 'pumps': {}})
        widget.set_state(None)
        widget.render(pix)

    def test_reservoir_labels_render_names_and_dim_unused(self):
        from PyQt6.QtGui import QPixmap
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Ibidi')
        widget = FluidSchematic()
        widget.resize(900, 500)
        widget.set_topology(svc.fluid_topology())
        labels = {
            rid: {'name': None, 'used': False, 'used_vol': 0, 'total_vol': 0}
            for rid in range(1, 25)
        }
        labels[1] = {'name': 'Imager 1', 'used': True,
                     'used_vol': 0, 'total_vol': 0}
        labels[8] = {'name': 'Buffer', 'used': True,
                     'used_vol': 150, 'total_vol': 600}
        widget.set_reservoir_labels(labels)
        # Stored and used in the hover tooltip (name + unused note + volume).
        self.assertEqual(widget._res_labels[8]['name'], 'Buffer')
        widget._update_hover_tooltip(8)
        self.assertIn('Buffer', widget.toolTip())
        self.assertIn('150 µl used', widget.toolTip())
        self.assertIn('600 µl needed', widget.toolTip())
        widget._update_hover_tooltip(2)
        self.assertIn('not used', widget.toolTip())
        # Rendering with labels + volume bars must not raise.
        widget.render(QPixmap(widget.size()))

    def test_renders_mvp_valve_topology(self):
        from PyQt6.QtGui import QPixmap
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Mercury')
        widget = FluidSchematic()
        widget.resize(1000, 560)
        widget.set_topology(svc.fluid_topology())
        # Valve 3 at its bridge port (1), valve 5 at port 8 -> reservoir 21.
        widget.set_state({
            'multiplexer': None,
            'valves': {3: 1, 5: 8, 1: 'in'},
            'pump_a': {'valve': 'in', 'volume': 100, 'capacity': 500},
            'pump_out': {'valve': 'out', 'volume': 0, 'capacity': 5000},
        })
        widget.set_reservoir_labels({
            1: {'name': 'Buffer', 'used': True,
                'used_vol': 200, 'total_vol': 800},
            21: {'name': 'Imager 8', 'used': True,
                 'used_vol': 0, 'total_vol': 703},
        })
        # Reservoir 21 is the fully-routed reservoir (its whole path matches).
        routes = widget._routes()
        self.assertEqual(
            widget._active_reservoir(routes, {3: 1, 5: 8}), 21
        )
        # Painting lays out the reservoir boxes (hit-test rects) and must not
        # raise; the box for reservoir 21 is then present for hover/click.
        widget.render(QPixmap(widget.size()))
        self.assertIn(21, widget._res_rects)
        widget._update_hover_tooltip(21)
        self.assertIn('valve 3 → port 1', widget.toolTip())
        self.assertIn('valve 5 → port 8', widget.toolTip())

    def test_highlight_reservoir_marks_its_full_route(self):
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Ibidi')
        widget = FluidSchematic()
        widget.set_topology(svc.fluid_topology())
        # No selection -> nothing highlighted.
        self.assertEqual(widget._active_highlight(), (None, set()))
        # Selecting a reservoir highlights every channel on its route.
        widget.highlight_reservoir(8)
        rid, channels = widget._active_highlight()
        self.assertEqual(rid, 8)
        self.assertEqual(channels, {1, 6, 12, 8})
        # A transient hover overrides the persistent selection.
        widget._hover_res = 23
        rid, channels = widget._active_highlight()
        self.assertEqual(rid, 23)
        self.assertEqual(channels, {1, 6, 7, 12, 13, 18, 24, 23})
        # Clearing the selection (hover gone) highlights nothing.
        widget._hover_res = None
        widget.highlight_reservoir(None)
        self.assertEqual(widget._active_highlight(), (None, set()))

    def test_hovering_a_port_highlights_and_emits(self):
        from PyQt6.QtGui import QPixmap, QMouseEvent
        from PyQt6.QtCore import QEvent, Qt
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Ibidi')
        widget = FluidSchematic()
        widget.resize(1000, 560)
        widget.set_topology(svc.fluid_topology())
        widget.render(QPixmap(widget.size()))   # populates the port hit-boxes

        hovered = []
        widget.reservoir_hovered.connect(hovered.append)
        pos = widget._port_rects[8].center()
        move = QMouseEvent(
            QEvent.Type.MouseMove, pos, pos, Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        widget.mouseMoveEvent(move)
        self.assertEqual(widget._hover_res, 8)
        self.assertEqual(hovered, [8])
        rid, channels = widget._active_highlight()
        self.assertEqual((rid, channels), (8, {1, 6, 12, 8}))
        # Leaving the widget clears the transient highlight.
        widget.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertIsNone(widget._hover_res)
        self.assertEqual(hovered, [8, None])

    def test_clicking_a_port_or_pump_emits_toggle_signals(self):
        from PyQt6.QtGui import QPixmap, QMouseEvent
        from PyQt6.QtCore import QEvent, Qt
        from PycroFlow.services import SystemService
        from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

        svc = SystemService()
        svc.load_setup('Ibidi')
        widget = FluidSchematic()
        widget.resize(1000, 560)
        widget.set_topology(svc.fluid_topology())
        widget.render(QPixmap(widget.size()))   # populates the hit-boxes

        channels, pumps = [], []
        widget.channel_clicked.connect(channels.append)
        widget.pump_clicked.connect(pumps.append)

        def click(pos):
            widget.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, pos, pos,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))

        click(widget._port_rects[8].center())
        click(widget._pump_rects['pump_a'].center())
        click(widget._pump_rects['pump_out'].center())
        self.assertEqual(channels, [8])
        self.assertEqual(pumps, ['pump_a', 'pump_out'])


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestFluidTabSchematic(unittest.TestCase):
    """The Fluid tab embeds the schematic and polls live state."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_tab_reloads_topology_and_pushes_state_on_refresh(self):
        from PycroFlow.services import SystemService
        from PycroFlow.gui.tabs.fluid_tab import FluidTab

        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        tab = FluidTab(svc)
        # Topology is rebuilt from the setup on refresh.
        tab.refresh()
        self.assertIsNotNone(tab.schematic._topo)
        self.assertEqual(tab.schematic._topo['multiplexer']['channels'], 24)
        # A live connection makes the polled snapshot flow to the widget.
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1', 2: 'R2'},
                         'special_names': {}},
        })
        svc.set_valves(2)
        tab._refresh_schematic_state()
        opened = [i + 1 for i, v in
                  enumerate(tab.schematic._state['multiplexer']['open']) if v]
        self.assertEqual(opened, [2])

    def test_reservoir_dropdown_drives_schematic_highlight(self):
        from PycroFlow.services import SystemService
        from PycroFlow.gui.tabs.fluid_tab import FluidTab

        svc = SystemService()
        svc.load_setup('Ibidi')
        tab = FluidTab(svc)
        tab.refresh()
        # Pick a reservoir in the manual "Set valves" dropdown; the schematic
        # highlights that reservoir's route.
        index = tab.valve_res.findData(8)
        self.assertGreaterEqual(index, 0)
        tab.valve_res.setCurrentIndex(index)
        rid, channels = tab.schematic._active_highlight()
        self.assertEqual(rid, 8)
        self.assertEqual(channels, {1, 6, 12, 8})

    def test_schematic_clicks_toggle_hardware_and_respect_run_lock(self):
        from PycroFlow.gui.widgets import worker
        from PycroFlow.services import SystemService
        from PycroFlow.gui.tabs.fluid_tab import FluidTab

        worker.set_synchronous(True)
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1', 2: 'R2'},
                         'special_names': {}},
        })
        tab = FluidTab(svc)
        tab.refresh()
        mux = svc.fluid_system.multiplexer

        # A port click toggles that raw channel through the service.
        self.assertFalse(mux.channel_states[6])
        tab.schematic.channel_clicked.emit(7)
        self.assertTrue(mux.channel_states[6])
        # A pump click flips its syringe valve.
        tab.schematic.pump_clicked.emit('pump_a')
        self.assertEqual(svc.fluid_system.pump_a.valve_pos, 'in')

        # While the orchestrator holds the run lock, clicks are ignored.
        tab.set_run_lock(True)
        tab.schematic.channel_clicked.emit(7)
        self.assertTrue(mux.channel_states[6])   # unchanged


if __name__ == "__main__":
    unittest.main()
