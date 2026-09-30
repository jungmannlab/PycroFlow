"""The operator module — the FIRST contributor to the composable shell (C32).

It contributes the sidebar (QC-at-a-glance) plus the four tab groups the V0.8 UI
is modelled on (C31): **Setup** · **Live QC** · **Analysis** · **Assistant**.
Each tab is a light :class:`~PycroFlow.gui.live.contribution.StreamConsumer` that
renders from the seam payloads; the heavy V0.8 analysis views (Average, Reference,
False-Color, 3D/MIP, Dye) are represented as placeholder panels here — the scope
flag in the brief (C31 open sub-question) defers deciding which are in the
automation product vs operator-only to a WP-4 look-and-feel run; they mount so
the layout is complete but own no compute.

The operator module registers itself via
:func:`PycroFlow.gui.live.contribution.register_contributor` at import time of
:func:`build_shell` (not at module import, to keep registration explicit + test-
isolated).
"""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.gui.live.advisor import SEVERITY_COLOR, FindingsAdapter
from PycroFlow.gui.live.contribution import PanelSpec
from PycroFlow.gui.live.inert import mark_inert


class _Placeholder(QWidget):
    """A mounted-but-empty analysis panel (scope-flagged; see module docstring)."""

    def __init__(self, title: str, note: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        head = QLabel(title + "  (planned)")
        head.setStyleSheet("font-weight: bold;")
        layout.addWidget(head)
        layout.addWidget(
            QLabel(note or "Renders from the stream at a WP-4 run.")
        )
        layout.addStretch()
        # Whole-panel placeholder: flag it so it reads as planned, not broken
        # (C31 open sub-question — which V0.8 analysis views ship in the product).
        mark_inert(self, "C31: analysis view scope")

    def on_update(self, update) -> None:  # pragma: no cover - inert panel
        pass


class SampleMetadataPanel(QWidget):
    """Setup · Sample-metadata -> qc.json descriptor + a completeness gate.

    Collects the ratified A2 descriptor axes (coarse only — leaf vocab is TBD/A18,
    so free text here) and exposes :meth:`descriptor` for the shell to write into
    qc.json. A completeness gate lights green once the required axes are filled.
    """

    completeness_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.sample_type = QLineEdit()
        self.target = QLineEdit()
        self.modality = QLineEdit()
        self.dimensionality = QLineEdit()
        self.buffer = QLineEdit()
        for label, w in (
            ("Sample type", self.sample_type),
            ("Target(s)", self.target),
            ("Modality", self.modality),
            ("Dimensionality", self.dimensionality),
            ("Buffer", self.buffer),
        ):
            w.textChanged.connect(self._check)
            form.addRow(label, w)
        self.gate = QLabel("incomplete")
        self.gate.setStyleSheet("color: {};".format(SEVERITY_COLOR["warning"]))
        form.addRow("Completeness", self.gate)

    def descriptor(self) -> dict:
        return {
            "sample_type": self.sample_type.text().strip(),
            "target": self.target.text().strip(),
            "modality": self.modality.text().strip(),
            "dimensionality": self.dimensionality.text().strip(),
            "buffer": self.buffer.text().strip(),
        }

    def is_complete(self) -> bool:
        return all(self.descriptor().values())

    def _check(self, *_: object) -> None:
        ok = self.is_complete()
        self.gate.setText("complete" if ok else "incomplete")
        self.gate.setStyleSheet(
            "color: {};".format(
                SEVERITY_COLOR["ok"] if ok else SEVERITY_COLOR["warning"]
            )
        )
        self.completeness_changed.emit(ok)

    def on_update(self, update) -> None:  # pragma: no cover - inert to stream
        pass


class LiveSignalPanel(QWidget):
    """Live QC · Live Signal: the metric readouts + a text sparkline trend + lag.

    The always-on numeric heartbeat (the sidebar carries the compact version;
    this is the fuller tab view). Subscribes to ``metrics``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trend: List[float] = []
        layout = QVBoxLayout(self)

        box = QGroupBox("Live metrics")
        v = QVBoxLayout(box)
        self.nena = QLabel("NeNA: —")
        self.locs = QLabel("localizations/frame: —")
        self.bg = QLabel("background: —")
        self.counts = QLabel("n_locs: —  ·  frames: —")
        for w in (self.nena, self.locs, self.bg, self.counts):
            v.addWidget(w)
        layout.addWidget(box)

        trend_box = QGroupBox("NeNA trend")
        tv = QVBoxLayout(trend_box)
        self.trend = QLabel("—")
        self.trend.setStyleSheet("font-family: monospace;")
        tv.addWidget(self.trend)
        layout.addWidget(trend_box)

        lag_box = QGroupBox("Pipeline lag (bounded queue)")
        lh = QHBoxLayout(lag_box)
        self.lag_bar = QProgressBar()
        self.lag_bar.setRange(0, 100)
        self.lag_bar.setFormat("%p% full")
        self.lag_label = QLabel("0/0")
        lh.addWidget(self.lag_bar)
        lh.addWidget(self.lag_label)
        layout.addWidget(lag_box)
        layout.addStretch()

    def on_update(self, update) -> None:
        if update.kind != "metrics":
            return
        m = update.payload.get("metrics", {}) or {}
        nena = m.get("nena_nm")
        self.nena.setText(
            "NeNA: {} nm".format(nena) if nena is not None else "NeNA: —"
        )
        spf = m.get("spots_per_frame")
        self.locs.setText(
            "localizations/frame: {}".format(spf)
            if spf is not None
            else "localizations/frame: —"
        )
        bg = m.get("background")
        self.bg.setText(
            "background: {}".format(bg) if bg is not None else "background: —"
        )
        self.counts.setText(
            "n_locs: {}  ·  frames: {}".format(
                m.get("n_locs"), m.get("n_frames")
            )
        )
        if nena is not None:
            self._trend.append(float(nena))
            self.trend.setText(_sparkline(self._trend))
        backend = update.payload.get("backend", {}) or {}
        depth = backend.get("queue_depth", 0) or 0
        size = backend.get("queue_size", 0) or 0
        self.lag_bar.setValue(int(100 * depth / size) if size else 0)
        self.lag_label.setText("{}/{}".format(depth, size))


class AdvisorPanel(QWidget):
    """Assistant · Advisor: the full findings list (sidebar shows only the light).

    Re-runs the injected advisor on each ``metrics`` update and lists the
    findings with severity colour, message and suggestion.
    """

    def __init__(self, advisor=None, parent=None):
        super().__init__(parent)
        self._advisor = advisor
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Advisor findings"))
        self.list = QPlainTextEdit()
        self.list.setReadOnly(True)
        layout.addWidget(self.list)
        # TODO(WP-ADVISOR): also render the filter-preview payload the Advisor
        # tab shows (the brief) once WP-ADVISOR emits it on the seam.

    def on_update(self, update) -> None:
        if update.kind != "metrics" or self._advisor is None:
            return
        m = update.payload.get("metrics", {}) or {}
        try:
            findings = FindingsAdapter.coerce_all(
                self._advisor.findings_for(m)
            )
        except Exception:
            return
        if not findings:
            self.list.setPlainText("all clear")
            return
        self.list.setPlainText(
            "\n".join(
                "[{}] {}{}".format(
                    f.severity.upper(),
                    f.message,
                    "\n    -> " + f.suggestion if f.suggestion else "",
                )
                for f in findings
            )
        )


class LogPanel(QWidget):
    """Live QC helper: the human log line stream (``log`` + ``record`` kinds)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Log"))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        layout.addWidget(self.view)

    def on_update(self, update) -> None:
        if update.kind == "log":
            self.view.appendPlainText(str(update.payload.get("message", "")))
        elif update.kind == "record":
            self.view.appendPlainText(
                "record posted: {}".format(update.payload.get("posted"))
            )
        elif update.kind == "state":
            self.view.appendPlainText(
                "state: {}".format(update.payload.get("state", ""))
            )


def _sparkline(values: List[float]) -> str:
    if not values:
        return "—"
    recent = values[-40:]
    lo, hi = min(recent), max(recent)
    blocks = "▁▂▃▄▅▆▇█"
    if hi - lo < 1e-9:
        return blocks[0] * len(recent)
    out = []
    for v in recent:
        idx = int((v - lo) / (hi - lo) * (len(blocks) - 1))
        out.append(blocks[idx])
    return "".join(out)


class OperatorContributor:
    """The operator module's :class:`PanelContributor` (C32).

    Contributes the four tab groups' panels. The sidebar QC-at-a-glance is added
    by the shell itself (it is the fixed rail), so it is not contributed here;
    the operator module owns the *tab* content.
    """

    module_id = "operator"

    def panels(self) -> List[PanelSpec]:
        def mk(title, note=""):
            return lambda ctx: _Placeholder(title, note)

        return [
            # Setup
            PanelSpec(
                "settings",
                "Settings",
                "Setup",
                mk("Settings", "Service + acquisition settings."),
                0,
            ),
            PanelSpec(
                "sample",
                "Sample",
                "Setup",
                lambda ctx: SampleMetadataPanel(),
                1,
            ),
            # Live QC
            PanelSpec(
                "live_signal",
                "Live Signal",
                "Live QC",
                lambda ctx: LiveSignalPanel(),
                0,
            ),
            PanelSpec("qc_map", "QC Map", "Live QC", mk("QC Map"), 1),
            PanelSpec(
                "precision_signal",
                "Precision + Signal",
                "Live QC",
                mk("Precision + Signal"),
                2,
            ),
            PanelSpec(
                "quality_check",
                "Quality Check",
                "Live QC",
                mk("Quality Check", "FRC curve + trend."),
                3,
            ),
            PanelSpec(
                "on_off", "On/Off Times", "Live QC", mk("On/Off Times"), 4
            ),
            PanelSpec("log", "Log", "Live QC", lambda ctx: LogPanel(), 5),
            # Analysis (scope-flagged placeholders)
            PanelSpec("trends", "Trends", "Analysis", mk("Trends"), 0),
            PanelSpec("drift", "Drift", "Analysis", mk("Drift"), 1),
            PanelSpec(
                "false_color", "False Color", "Analysis", mk("False Color"), 2
            ),
            PanelSpec("mip", "3D / MIP", "Analysis", mk("3D / MIP"), 3),
            PanelSpec("picks", "Picks", "Analysis", mk("Picks"), 4),
            PanelSpec(
                "average",
                "Average",
                "Analysis",
                mk(
                    "Average", "Scope-flag: automation vs operator-only (C31)."
                ),
                5,
            ),
            PanelSpec(
                "dye", "Dye Analysis", "Analysis", mk("Dye Analysis"), 6
            ),
            PanelSpec(
                "reference",
                "Reference",
                "Analysis",
                mk(
                    "Reference",
                    "Scope-flag: automation vs operator-only (C31).",
                ),
                7,
            ),
            # Assistant
            PanelSpec(
                "advisor",
                "Advisor",
                "Assistant",
                lambda ctx: AdvisorPanel(ctx.advisor),
                0,
            ),
            PanelSpec("protocol", "Protocol", "Assistant", mk("Protocol"), 1),
            PanelSpec("chatbot", "Chatbot", "Assistant", mk("Chatbot"), 2),
        ]


__all__ = [
    "OperatorContributor",
    "SampleMetadataPanel",
    "LiveSignalPanel",
    "AdvisorPanel",
    "LogPanel",
]
