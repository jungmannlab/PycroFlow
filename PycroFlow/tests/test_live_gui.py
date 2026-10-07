"""Headless mock-stream tests for WP-GUI (the composable operator frontend).

Everything here runs with no instrument and no real acquisition: a fake service
exposes WP-4's seam surface (``hub`` + ``request_abort``) and we drive the shell
by pushing :class:`~PycroFlow.live_analysis.client_seam.LiveUpdate`\\ s through the
real :class:`UpdateHub`. Covered:

* headless import safety (``PycroFlow.gui`` must not import PyQt6 at package load);
* the shell builds with the four operator tab groups + the fixed sidebar/overview;
* metric / thumbnail / state updates render (the thin subscribing client);
* the early-abort control call reaches the service;
* multi-client attach (two shells, one run) — both update; detach isolates;
* the advisor-findings adapter renders against a mock advisor, and the adapter
  coerces an arbitrary duck-typed finding object (the WP-ADVISOR drop-in seam);
* the C32 contribution registry mounts a custom contributor's group.

Skipped entirely when PyQt6 is absent (a minimal CI job without the [gui] extra).
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt6  # noqa: F401

    _HAVE_PYQT6 = True
except ImportError:
    _HAVE_PYQT6 = False

from PycroFlow.live_analysis.client_seam import UpdateHub  # noqa: E402


class FakeService:
    """Duck-typed WP-4 service: just the seam surface the GUI depends on."""

    def __init__(self):
        self.hub = UpdateHub()
        self.aborted = False

    def request_abort(self):
        self.aborted = True


def _app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _QtTestCase(unittest.TestCase):
    """Base for Qt tests: one shared QApplication, keep widgets alive.

    Widgets are appended to :attr:`_alive` and only released at class teardown —
    letting a top-level Qt widget get garbage-collected *between* tests aborts
    under the offscreen platform, so we keep references for the class lifetime.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls._alive = []

    @classmethod
    def tearDownClass(cls):
        for w in getattr(cls, "_alive", []):
            try:
                w.close_client()
            except Exception:
                pass
        cls._alive = []
        cls.app.processEvents()

    def _make_shell(self, **kwargs):
        from PycroFlow.gui.live.shell import LiveShell

        shell = LiveShell(**kwargs)
        self._alive.append(shell)
        return shell


# ── import safety (no PyQt6 needed) ─────────────────────────────────────────


class TestImportSafety(unittest.TestCase):
    def test_gui_package_import_does_not_require_pyqt6(self):
        import importlib
        import sys

        before = "PyQt6" in sys.modules
        importlib.import_module("PycroFlow.gui")
        if not before:
            self.assertNotIn("PyQt6", sys.modules)

    def test_advisor_adapter_imports_without_qt(self):
        # The adapter is pure-Python and must be usable off the GUI thread.
        from PycroFlow.gui.live.advisor import Finding, worst_severity

        fs = [Finding("info", "a"), Finding("error", "b")]
        self.assertEqual(worst_severity(fs), "error")
        self.assertEqual(worst_severity([]), "ok")


# ── advisor adapter (WP-ADVISOR drop-in seam) ───────────────────────────────


class TestAdvisorAdapter(unittest.TestCase):
    def test_coerce_duck_typed_finding(self):
        from PycroFlow.gui.live.advisor import FindingsAdapter

        class Raw:  # what a real qc_advisor finding might look like
            severity = "warning"
            message = "high background"
            suggestion = "lower laser"
            source = "bg"

        f = FindingsAdapter.coerce(Raw())
        self.assertEqual(f.severity, "warning")
        self.assertEqual(f.message, "high background")
        self.assertEqual(f.suggestion, "lower laser")

    def test_coerce_dict_finding(self):
        from PycroFlow.gui.live.advisor import FindingsAdapter

        f = FindingsAdapter.coerce({"severity": "error", "message": "drift"})
        self.assertEqual(f.severity, "error")
        self.assertIsNone(f.suggestion)

    def test_coerce_wp_advisor_finding(self):
        # The real picasso_workflow.qc_advisor Finding: severity vocab is
        # bad/warn/info/ok and the suggestion field is named "action" (plus
        # extra fields metric/cause/value/detail). The adapter must map
        # bad->error (NOT silently drop to info) and action->suggestion.
        from PycroFlow.gui.live.advisor import FindingsAdapter

        class QcAdvisorFinding:  # shape of picasso_workflow.qc_advisor.Finding
            metric = "nena"
            severity = "bad"
            message = "NeNA very high"
            cause = "drift/focus"
            action = "abort and refocus"
            value = 18.0
            source = "diagnose"
            detail = {}

        f = FindingsAdapter.coerce(QcAdvisorFinding())
        self.assertEqual(f.severity, "error")  # bad -> error, not info
        self.assertEqual(
            f.suggestion, "abort and refocus"
        )  # action -> suggestion
        self.assertEqual(f.source, "diagnose")

        # warn -> warning, via dict shape + as_dict()-style keys
        w = FindingsAdapter.coerce(
            {"severity": "warn", "message": "bg high", "action": "lower power"}
        )
        self.assertEqual(w.severity, "warning")
        self.assertEqual(w.suggestion, "lower power")

    def test_unknown_severity_fails_loud_not_silent(self):
        # An unrecognised tier (e.g. a future qc_advisor "critical"/"fatal")
        # must escalate to "error", NOT degrade to "info" — otherwise a
        # critical finding would rank least severe and never light the sidebar.
        from PycroFlow.gui.live.advisor import (
            FindingsAdapter,
            worst_severity,
        )

        f = FindingsAdapter.coerce(
            {"severity": "critical", "message": "meltdown"}
        )
        self.assertEqual(f.severity, "error")
        g = FindingsAdapter.coerce({"severity": "fatal", "message": "x"})
        self.assertEqual(g.severity, "error")
        # And it dominates the sidebar light over a mere warning.
        self.assertEqual(
            worst_severity(
                [
                    FindingsAdapter.coerce(
                        {"severity": "warn", "message": "w"}
                    ),
                    f,
                ]
            ),
            "error",
        )

    def test_wrap_real_advisor_coerces_output(self):
        from PycroFlow.gui.live.advisor import Finding, FindingsAdapter

        class RealAdvisor:
            def findings_for(self, metrics):
                return [{"severity": "info", "message": "ok-ish"}]

        wrapped = FindingsAdapter.wrap(RealAdvisor())
        out = wrapped.findings_for({})
        self.assertIsInstance(out[0], Finding)

    def test_mock_advisor_thresholds(self):
        from PycroFlow.gui.live.advisor import MockAdvisor

        adv = MockAdvisor()
        self.assertTrue(adv.findings_for({"nena_nm": 20.0}))
        self.assertFalse(adv.findings_for({"nena_nm": 3.0}))


# ── contribution registry (C32) ─────────────────────────────────────────────


class TestContribution(unittest.TestCase):
    def test_collect_panels_sorted_by_group(self):
        from PycroFlow.gui.live.contribution import (
            PanelSpec,
            collect_panels,
        )

        class C:
            module_id = "x"

            def panels(self):
                return [
                    PanelSpec("b", "B", "Analysis", lambda ctx: None, 0),
                    PanelSpec("a", "A", "Setup", lambda ctx: None, 0),
                ]

        specs = collect_panels([C()])
        self.assertEqual([s.group for s in specs], ["Setup", "Analysis"])


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestShellMockStream(_QtTestCase):
    def setUp(self):
        self.svc = FakeService()
        self.shell = self._make_shell(service=self.svc)

    def test_builds_four_operator_groups(self):
        titles = [
            self.shell.tab_groups.tabText(i)
            for i in range(self.shell.tab_groups.count())
        ]
        self.assertEqual(titles, ["Setup", "Live QC", "Analysis", "Assistant"])

    def test_metrics_update_renders(self):
        self.svc.hub.push_kind("state", "run1", state="fov_started")
        self.svc.hub.push_kind(
            "metrics",
            "run1",
            metrics={
                "nena_nm": 6.5,
                "spots_per_frame": 0.03,
                "n_locs": 500,
                "n_frames": 100,
                "background": 90.0,
            },
            backend={"queue_depth": 2, "queue_size": 8},
        )
        self.assertEqual(
            self.shell.sidebar._value_labels["nena_nm"].text(), "6.5"
        )
        # Exact rendered text (the loose "100" substring hid formatting bugs).
        self.assertEqual(
            self.shell.frame_label.text(), "frames: 100  ·  locs: 500"
        )
        self.assertEqual(self.shell.status_label.text(), "state: fov_started")

    def test_early_abort_control_call(self):
        self.svc.hub.push_kind("state", "run1", state="fov_started")
        self.shell.sidebar.abort_requested.emit()
        self.assertTrue(self.svc.aborted)

    def test_thumbnail_update_reaches_overview(self):
        self.svc.hub.push_kind(
            "thumbnail", "run1", shape=(64, 64), pixelsize_nm=130.0
        )
        self.assertIn("nm/px", self.shell.overview.scale_bar.text())

    def test_advisor_light_reflects_findings(self):
        # A bad NeNA -> the mock advisor emits an error -> the light goes RED
        # (the actual severity signal, not just the text).
        from PycroFlow.gui.live.advisor import SEVERITY_COLOR

        self.svc.hub.push_kind(
            "metrics", "run1", metrics={"nena_nm": 25.0}, backend={}
        )
        self.assertIn("NeNA", self.shell.sidebar.advisor_text.text())
        self.assertIn(
            SEVERITY_COLOR["error"],
            self.shell.sidebar.advisor_light.styleSheet(),
        )

    def test_advisor_light_green_when_clear(self):
        from PycroFlow.gui.live.advisor import SEVERITY_COLOR

        self.svc.hub.push_kind(
            "metrics", "run1", metrics={"nena_nm": 3.0}, backend={}
        )
        self.assertEqual(self.shell.sidebar.advisor_text.text(), "all clear")
        self.assertIn(
            SEVERITY_COLOR["ok"],
            self.shell.sidebar.advisor_light.styleSheet(),
        )

    def test_abort_disabled_when_idle(self):
        self.svc.hub.push_kind("state", "run1", state="fov_done")
        self.assertFalse(self.shell.sidebar.abort_btn.isEnabled())


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestMultiClient(_QtTestCase):
    def test_two_shells_one_run_then_detach(self):
        svc = FakeService()
        s1 = self._make_shell(service=svc)
        s2 = self._make_shell(service=svc)

        # Both bridges are registered with the one hub.
        self.assertEqual(len(svc.hub._clients), 2)

        svc.hub.push_kind(
            "metrics", "r", metrics={"nena_nm": 10.0}, backend={}
        )
        self.assertEqual(s1.sidebar._value_labels["nena_nm"].text(), "10")
        self.assertEqual(s2.sidebar._value_labels["nena_nm"].text(), "10")

        # Detaching s2 must not affect s1 (per-shell subscription) and must
        # release s2's slot back to baseline (no leak).
        s2.close_client()
        self.assertEqual(len(svc.hub._clients), 1)
        svc.hub.push_kind("metrics", "r", metrics={"nena_nm": 4.0}, backend={})
        self.assertEqual(s1.sidebar._value_labels["nena_nm"].text(), "4")
        self.assertEqual(s2.sidebar._value_labels["nena_nm"].text(), "10")


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestCustomContributor(_QtTestCase):
    def test_custom_group_mounts(self):
        from PycroFlow.gui.live.contribution import PanelSpec
        from PycroFlow.gui.live.operator import OperatorContributor
        from PyQt6.QtWidgets import QLabel

        class Dash:
            module_id = "dashboard"

            def panels(self):
                return [
                    PanelSpec(
                        "fleet",
                        "Fleet",
                        "Dashboard",
                        lambda ctx: QLabel("fleet"),
                        0,
                    )
                ]

        shell = self._make_shell(
            service=FakeService(),
            contributors=[OperatorContributor(), Dash()],
        )
        titles = [
            shell.tab_groups.tabText(i)
            for i in range(shell.tab_groups.count())
        ]
        # Operator groups first (canonical order), then the custom group.
        self.assertEqual(
            titles, ["Setup", "Live QC", "Analysis", "Assistant", "Dashboard"]
        )

    def _titles(self, shell):
        return [
            shell.tab_groups.tabText(i)
            for i in range(shell.tab_groups.count())
        ]

    def test_explicit_contributors_are_isolated(self):
        # Two shells with DIFFERENT explicit contributor sets in one process
        # must show different tab groups (no shared global state), and an
        # explicit-contributors shell must NOT register into the global
        # registry (so a later default shell isn't polluted by it).
        from PycroFlow.gui.live.contribution import (
            PanelSpec,
            clear_contributors,
            iter_contributors,
        )
        from PycroFlow.gui.live.operator import OperatorContributor
        from PyQt6.QtWidgets import QLabel

        clear_contributors()
        self.addCleanup(clear_contributors)

        class Dash:
            module_id = "dashboard"

            def panels(self):
                return [
                    PanelSpec(
                        "fleet",
                        "Fleet",
                        "Dashboard",
                        lambda ctx: QLabel("fleet"),
                        0,
                    )
                ]

        s_full = self._make_shell(
            service=FakeService(),
            contributors=[OperatorContributor(), Dash()],
        )
        s_op = self._make_shell(
            service=FakeService(), contributors=[OperatorContributor()]
        )

        self.assertIn("Dashboard", self._titles(s_full))
        self.assertNotIn("Dashboard", self._titles(s_op))
        # Neither explicit shell polluted the process-global registry.
        self.assertEqual(iter_contributors(), [])

        # A default (contributors=None) shell falls back to the global registry
        # (with the operator module ensured) and is unpolluted by the above.
        s_default = self._make_shell(service=FakeService())
        self.assertEqual(
            self._titles(s_default),
            ["Setup", "Live QC", "Analysis", "Assistant"],
        )


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestOperatorPanels(_QtTestCase):
    def _mk(self, cls, *args):
        # Keep a reference for the class lifetime so the widget isn't GC'd
        # mid-run (aborts under offscreen Qt). tearDownClass's close_client()
        # call is wrapped in try/except, so a plain widget is fine here.
        w = cls(*args)
        self._alive.append(w)
        return w

    def test_live_signal_panel_renders_and_guards_zero_queue(self):
        from PycroFlow.gui.live.client import LiveUpdate
        from PycroFlow.gui.live.operator import LiveSignalPanel

        p = self._mk(LiveSignalPanel)
        # queue_size 0 must not ZeroDivisionError (the guard).
        p.on_update(
            LiveUpdate(
                "metrics",
                "r",
                {
                    "metrics": {
                        "nena_nm": 5.0,
                        "spots_per_frame": 0.02,
                        "background": 88.0,
                        "n_locs": 300,
                        "n_frames": 120,
                    },
                    "backend": {"queue_depth": 0, "queue_size": 0},
                },
            )
        )
        self.assertIn("5.0", p.nena.text())
        self.assertEqual(p.lag_bar.value(), 0)
        # A non-empty queue fills the bar.
        p.on_update(
            LiveUpdate(
                "metrics",
                "r",
                {
                    "metrics": {"nena_nm": 5.0},
                    "backend": {"queue_depth": 4, "queue_size": 8},
                },
            )
        )
        self.assertEqual(p.lag_bar.value(), 50)

    def test_log_panel_appends_lines(self):
        from PycroFlow.gui.live.client import LiveUpdate
        from PycroFlow.gui.live.operator import LogPanel

        p = self._mk(LogPanel)
        p.on_update(LiveUpdate("log", "r", {"message": "hello"}))
        p.on_update(LiveUpdate("state", "r", {"state": "fov_done"}))
        p.on_update(LiveUpdate("record", "r", {"posted": True}))
        text = p.view.toPlainText()
        self.assertIn("hello", text)
        self.assertIn("fov_done", text)
        self.assertIn("record posted: True", text)

    def test_advisor_panel_lists_findings(self):
        from PycroFlow.gui.live.advisor import MockAdvisor
        from PycroFlow.gui.live.client import LiveUpdate
        from PycroFlow.gui.live.operator import AdvisorPanel

        p = self._mk(AdvisorPanel, MockAdvisor())
        p.on_update(LiveUpdate("metrics", "r", {"metrics": {"nena_nm": 25.0}}))
        self.assertIn("NeNA", p.list.toPlainText())
        # Clear metrics -> "all clear".
        p.on_update(LiveUpdate("metrics", "r", {"metrics": {"nena_nm": 3.0}}))
        self.assertEqual(p.list.toPlainText(), "all clear")


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestCrossThreadMarshalling(_QtTestCase):
    def test_update_from_worker_thread_is_queued_not_synchronous(self):
        # The bridge's whole purpose: an update pushed from a NON-GUI thread
        # must be QUEUED onto the GUI event loop, not applied synchronously off
        # the GUI thread. So the widget stays unchanged until processEvents().
        import threading

        svc = FakeService()
        shell = self._make_shell(service=svc)
        label = shell.sidebar._value_labels["nena_nm"]
        self.assertEqual(label.text(), "—")

        done = threading.Event()

        def worker():
            svc.hub.push_kind(
                "metrics", "r", metrics={"nena_nm": 7.0}, backend={}
            )
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertTrue(done.wait(2.0))
        # Queued, not applied: still the placeholder before we pump events.
        self.assertEqual(label.text(), "—")
        # Pump the GUI event loop -> the queued signal is delivered.
        self.app.processEvents()
        self.assertEqual(label.text(), "7")


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestInertControls(_QtTestCase):
    """Pins the set of controls flagged inert (planned, not yet wired).

    A FORCING FUNCTION: wiring a control along the plan means deleting its
    ``mark_inert(...)`` call, which drops it from this set and fails this test
    until :attr:`EXPECTED_INERT_BUTTONS` is updated to match. So the "inert" flag
    can neither outlive the wiring (a wired-but-still-flagged control) nor linger
    unnoticed — keeping the UI and these tests honest about what does something.
    See :mod:`PycroFlow.gui.live.inert`.
    """

    EXPECTED_INERT_BUTTONS = {
        # top bar (shell) — not wired to the service yet
        "Start",
        "Stop",
        "Skip",
        "Save locs",
        "Update qc.json",
        # sidebar core controls (emit signals nothing consumes yet)
        "estimate",
        "Undrift now",
        "Filter preview",
    }

    def _inert_buttons(self, shell):
        from PyQt6.QtWidgets import QPushButton

        from PycroFlow.gui.live.inert import is_inert

        return {
            b.text() for b in shell.findChildren(QPushButton) if is_inert(b)
        }

    def test_inert_buttons_match_expected(self):
        shell = self._make_shell()
        self.assertEqual(
            self._inert_buttons(shell), self.EXPECTED_INERT_BUTTONS
        )

    def test_wired_controls_are_not_flagged(self):
        # Guard against over-marking: the working controls must NOT be inert.
        from PycroFlow.gui.live.inert import is_inert

        shell = self._make_shell()
        self.assertFalse(is_inert(shell.sidebar.abort_btn))
        self.assertFalse(is_inert(shell.overview.auto_btn))
        self.assertFalse(is_inert(shell.overview.boxes_cb))

    def test_placeholder_panels_are_flagged(self):
        from PycroFlow.gui.live.inert import is_inert
        from PycroFlow.gui.live.operator import _Placeholder

        shell = self._make_shell()
        placeholders = shell.findChildren(_Placeholder)
        self.assertTrue(placeholders)  # there ARE placeholder analysis views
        self.assertTrue(all(is_inert(p) for p in placeholders))


if __name__ == "__main__":
    unittest.main()


class TestPerFrameBoxes(_QtTestCase):
    """Boxes are per-frame: a thumbnail without boxes clears the overlay."""

    def test_stale_boxes_cleared_on_boxless_frame(self):
        import numpy as np

        from PycroFlow.gui.live.panels import OverviewZoom

        panel = OverviewZoom()
        frame = np.zeros((32, 32), dtype=np.uint16)
        panel._on_thumbnail(
            {
                "data": frame.tobytes(),
                "shape": frame.shape,
                "dtype": "uint16",
                "boxes": [10.0, 12.0],
                "box_size": 7,
            }
        )
        self.assertEqual(panel._boxes, [10.0, 12.0])
        # The next frame arrives WITHOUT boxes (identify failed / overlay
        # off): the previous frame's detections must NOT stay drawn.
        panel._on_thumbnail(
            {"data": frame.tobytes(), "shape": frame.shape, "dtype": "uint16"}
        )
        self.assertEqual(panel._boxes, [])


class TestAutoContrastIgnore(_QtTestCase):
    """MM-style 'ignore %' reference for the Overview's Auto contrast."""

    def test_auto_uses_ignore_percentiles(self):
        import numpy as np

        from PycroFlow.gui.live.panels import OverviewZoom

        panel = OverviewZoom()
        raw = np.arange(10000, dtype=np.uint16).reshape(100, 100)
        panel._set_image_from_bytes(raw.tobytes(), raw.shape, "uint16")

        panel.ignore_pct.setValue(10.0)  # valueChanged triggers auto
        self.assertEqual(panel.black.value(), int(np.percentile(raw, 10)))
        self.assertEqual(panel.white.value(), int(np.percentile(raw, 90)))

        # 0 % = true min/max, like MM with the reference at zero.
        panel.ignore_pct.setValue(0.0)
        panel._auto_contrast()
        self.assertEqual(panel.black.value(), 0)
        self.assertEqual(panel.white.value(), 9999)

    def test_latched_auto_restretches_each_frame_until_manual_edit(self):
        import numpy as np

        from PycroFlow.gui.live.panels import OverviewZoom

        panel = OverviewZoom()
        panel.ignore_pct.setValue(0.0)
        dim = np.full((16, 16), 50, dtype=np.uint16)
        dim[0, 0], dim[0, 1] = 10, 200
        bright = (dim * 100).astype(np.uint16)

        panel.auto_btn.setChecked(True)  # latch -> MM-style autostretch
        panel._set_image_from_bytes(dim.tobytes(), dim.shape, "uint16")
        self.assertEqual((panel.black.value(), panel.white.value()), (10, 200))
        panel._set_image_from_bytes(bright.tobytes(), bright.shape, "uint16")
        self.assertEqual(
            (panel.black.value(), panel.white.value()), (1000, 20000)
        )

        # A manual black/white edit takes back control: auto unlatches and
        # the next frame no longer overwrites the chosen window.
        panel.black.setValue(42)
        self.assertFalse(panel.auto_btn.isChecked())
        panel._set_image_from_bytes(dim.tobytes(), dim.shape, "uint16")
        self.assertEqual(panel.black.value(), 42)
