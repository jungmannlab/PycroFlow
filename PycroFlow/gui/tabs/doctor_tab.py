"""Doctor tab: health checks for subsystems and external connectors.

A single place that *verifies* — as far as is cheaply possible — which
subsystems (fluid / imaging / illumination) and connectors (picasso-registry,
monet) are actually working, so problems are easier to disentangle when a run
misbehaves. The checks themselves live in the frontend-agnostic
:class:`PycroFlow.services.DiagnosticsService` (the CLI's ``doctor`` command
shares them); this tab only renders the results and runs them off the GUI
thread (they do serial + network I/O).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.services import CheckStatus, DiagnosticsService, summarize
from PycroFlow.gui.widgets.worker import run_in_background

# Per-status glyph + colour for the tree cells.
_STATUS_STYLE = {
    CheckStatus.OK: ("✓ ok", QColor("#2e7d32")),
    CheckStatus.WARN: ("⚠ warn", QColor("#b8860b")),
    CheckStatus.FAIL: ("✗ fail", QColor("#c62828")),
    CheckStatus.SKIP: ("– skip", QColor("#777777")),
}


class DoctorTab(QWidget):
    """Render :class:`DiagnosticsService` results in a grouped, colour-coded
    tree with a Run button and a one-line summary."""

    def __init__(self, system_service, experiment_service=None, parent=None):
        super().__init__(parent)
        self._sys = system_service
        self._exp = experiment_service
        self._has_run = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.run_btn = QPushButton("Run diagnostics")
        self.run_btn.clicked.connect(self.run_diagnostics)
        header.addWidget(self.run_btn)
        self.summary_label = QLabel("not run yet")
        header.addWidget(self.summary_label)
        header.addStretch()
        layout.addLayout(header)

        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #777777;")
        layout.addWidget(self.note_label)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Check", "Status", "Detail"])
        self.tree.setRootIsDecorated(True)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        header_view.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

    # -- running --------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802 - Qt override
        """Auto-run once the first time the tab is shown."""
        super().showEvent(event)
        if not self._has_run:
            self.run_diagnostics()

    def run_diagnostics(self):
        """Run all checks off the GUI thread and repopulate the tree."""
        self._has_run = True
        self.run_btn.setEnabled(False)
        self.summary_label.setText("running…")

        sys_service = self._sys
        exp_service = self._exp

        def work():
            service = DiagnosticsService(sys_service, exp_service)
            return service.run_all()

        run_in_background(
            self, work, on_done=self._populate, on_error=self._on_error
        )

    def _on_error(self, exc):
        self.run_btn.setEnabled(True)
        self.summary_label.setText("error: {!r}".format(exc))

    def _populate(self, results):
        self.run_btn.setEnabled(True)
        self.tree.clear()

        # Group by category, preserving first-seen order.
        order = []
        groups = {}
        for result in results:
            if result.category not in groups:
                groups[result.category] = []
                order.append(result.category)
            groups[result.category].append(result)

        for category in order:
            parent = QTreeWidgetItem([category, "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.tree.addTopLevelItem(parent)
            for result in groups[category]:
                self._add_row(parent, result)
            parent.setExpanded(True)

        counts = summarize(results)
        self.summary_label.setText(
            "{} ok · {} warn · {} fail · {} skip".format(
                counts[CheckStatus.OK],
                counts[CheckStatus.WARN],
                counts[CheckStatus.FAIL],
                counts[CheckStatus.SKIP],
            )
        )
        if (
            self._exp is not None
            and DiagnosticsService(self._sys, self._exp).run_active()
        ):
            self.note_label.setText(
                "A run is in progress — instrument pings were skipped so they "
                "do not contend with the orchestrator for the bus."
            )
        else:
            self.note_label.setText("")

    def _add_row(self, parent, result):
        label, colour = _STATUS_STYLE.get(
            result.status, (result.status.value, QColor("#000000"))
        )
        item = QTreeWidgetItem([result.name, label, result.detail])
        item.setForeground(1, QBrush(colour))
        if result.duration_s is not None:
            item.setToolTip(1, "{:.0f} ms".format(result.duration_s * 1000))
        item.setData(0, Qt.ItemDataRole.UserRole, result.status)
        parent.addChild(item)

    # -- coordinator hook -----------------------------------------------------
    def refresh(self):
        """No-op: results are refreshed on demand (Run button / tab show).

        Kept for parity with the other tabs' ``refresh()`` so the main window
        can call it uniformly after a connect without forcing serial/network
        I/O on every connection change.
        """
        return
