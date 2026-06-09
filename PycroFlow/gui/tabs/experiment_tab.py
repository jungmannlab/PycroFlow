"""Run Sequence tab: watch a loaded protocol run — state, progress, log.

Drives :class:`PycroFlow.services.experiment_service.ExperimentService` and
subscribes to a :class:`PycroFlow.gui.qt_bridge.QtBridge` for thread-safe
state/log updates. Loading and the run controls (Start/Pause/Resume/Abort)
live in the main-window toolbar — the single, always-visible control surface —
so this tab is a pure view.
"""
import ast

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget,
    QPlainTextEdit, QGroupBox, QTableWidget, QTableWidgetItem,
    QMessageBox, QProgressBar,
)

from PycroFlow.services.experiment_service import ExperimentState
from PycroFlow.gui.widgets.dnd import YamlDropMixin


# States during which we poll the orchestrator for live progress.
_ACTIVE_STATES = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
                  ExperimentState.PAUSED}

# Step-list shading.
_FINISHED_COLOR = QColor("#e8f5e9")   # light green — completed
_ACTIVE_COLOR = QColor("#fff59d")     # amber — currently executing


class ExperimentTab(YamlDropMixin, QWidget):
    def __init__(self, service, bridge, parent=None):
        super().__init__(parent)
        self._service = service
        self._bridge = bridge
        self.enable_yaml_drop()
        # Backing store for the step list so a selection can look up the
        # full entry dict (the list only shows index + $type). Entries are
        # references into the loaded protocol, so editing them in the table
        # mutates the protocol the orchestrator runs.
        self._step_entries = []
        # (system, index) per list row, aligned with _step_entries, for
        # progress shading.
        self._step_meta = []
        self._current_step = -1
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("State:"))
        self.state_label = QLabel(self._service.state.value)
        self.state_label.setObjectName("state_label")
        status_row.addWidget(self.state_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # --- progress
        prog_box = QGroupBox("Progress")
        prog_layout = QVBoxLayout(prog_box)
        overall_row = QHBoxLayout()
        overall_row.addWidget(QLabel("Overall:"))
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        overall_row.addWidget(self.overall_bar)
        prog_layout.addLayout(overall_row)
        round_row = QHBoxLayout()
        round_row.addWidget(QLabel("Rounds:"))
        self.round_bar = QProgressBar()
        self.round_bar.setRange(0, 100)
        round_row.addWidget(self.round_bar)
        prog_layout.addLayout(round_row)
        self.step_status = QLabel("—")
        prog_layout.addWidget(self.step_status)
        layout.addWidget(prog_box)

        # --- step list + per-step parameters
        steps_box = QGroupBox("Protocol steps")
        steps_layout = QHBoxLayout(steps_box)
        self.step_list = QListWidget()
        steps_layout.addWidget(self.step_list, 1)

        params_box = QGroupBox("Step parameters (editable)")
        params_layout = QVBoxLayout(params_box)
        self.step_table = QTableWidget(0, 2)
        self.step_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.step_table.horizontalHeader().setStretchLastSection(True)
        self.step_table.verticalHeader().setVisible(False)
        params_layout.addWidget(self.step_table)
        self.apply_btn = QPushButton("Apply changes")
        self.apply_btn.setEnabled(False)
        params_layout.addWidget(self.apply_btn)
        steps_layout.addWidget(params_box, 1)

        layout.addWidget(steps_box)

        # --- log pane
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box)

    def _connect_signals(self):
        self._bridge.state_changed.connect(self._on_state_changed)
        self._bridge.log_message.connect(self._on_log)
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        self.apply_btn.clicked.connect(self._on_apply)
        self._poll_timer.timeout.connect(self._poll_progress)

    # --- loading (controls live in the main-window toolbar)

    def load_protocol_path(self, path):
        """Programmatic load (used by the toolbar / drag&drop / tests)."""
        self._service.load_protocol_from_yaml(path)
        self._populate_steps()

    def on_yaml_dropped(self, path):
        """Load a Run Sequence YAML dropped onto the tab."""
        self.load_protocol_path(path)

    # --- bridge-driven UI updates (run on the GUI thread)

    def _on_state_changed(self, old, new):
        self.state_label.setText(new.value)
        # Repopulate the step list whenever a protocol becomes loaded,
        # regardless of how it was loaded (button, toolbar, or programmatic),
        # so the view always reflects the active protocol.
        if new is ExperimentState.LOADED:
            self._populate_steps()
        # Poll for live progress while the orchestrator runs; one immediate
        # update so the bars reflect the new state right away.
        if new in _ACTIVE_STATES:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._poll_timer.stop()
        self._poll_progress()

    def _on_log(self, message):
        self.log_view.appendPlainText(message)

    # --- helpers

    def _populate_steps(self):
        self.step_list.clear()
        self._step_entries = []
        self._step_meta = []
        protocol = self._service.protocol or {}
        # List each subsystem's steps, prefixed with the subsystem so the
        # combined view is unambiguous. _step_entries / _step_meta stay
        # aligned with the list rows (for the param view and progress shading).
        for system in ('fluid', 'img', 'illu'):
            sub = protocol.get(system, {})
            entries = (
                sub.get('protocol_entries', []) if isinstance(sub, dict)
                else [])
            for i, entry in enumerate(entries):
                type_ = (
                    entry.get('$type', '?') if isinstance(entry, dict)
                    else '?')
                self.step_list.addItem(
                    "[{}] {:d}: {}".format(system, i, type_))
                self._step_entries.append(entry)
                self._step_meta.append((system, i))
        self._current_step = -1
        self.step_table.setRowCount(0)
        self.apply_btn.setEnabled(False)

    # --- live progress

    def _poll_progress(self):
        prog = self._service.progress()
        if not prog:
            return
        done = sum(c for c, _ in prog.values())
        total = sum(t for _, t in prog.values())
        pct = int(100 * done / total) if total else 0
        self.overall_bar.setValue(pct)
        self.overall_bar.setFormat("{}/{} steps (%p%)".format(done, total))

        parts = []
        for key in ('fluid', 'img', 'illu'):
            if key in prog:
                cur, tot = prog[key]
                parts.append("{} {}/{}".format(key, cur, tot))
        self.step_status.setText("   ·   ".join(parts))

        self._update_round_bar(prog.get('img'))
        self._shade_steps(prog)

    def _update_round_bar(self, img_prog):
        protocol = self._service.protocol or {}
        img = protocol.get('img', {})
        entries = (
            img.get('protocol_entries', []) if isinstance(img, dict) else [])
        acquire_idx = [
            i for i, e in enumerate(entries)
            if isinstance(e, dict) and e.get('$type') == 'acquire']
        total_rounds = len(acquire_idx)
        if not total_rounds:
            self.round_bar.setValue(0)
            self.round_bar.setFormat("no imaging rounds")
            return
        cur = img_prog[0] if img_prog else 0
        done_rounds = sum(1 for i in acquire_idx if i < cur)
        self.round_bar.setValue(int(100 * done_rounds / total_rounds))
        self.round_bar.setFormat(
            "round {}/{}".format(done_rounds, total_rounds))

    def _shade_steps(self, prog):
        active = self._service.state in _ACTIVE_STATES
        for row, (system, idx) in enumerate(self._step_meta):
            item = self.step_list.item(row)
            if item is None:
                continue
            cur, _ = prog.get(system, (0, 0))
            if idx < cur:
                item.setBackground(_FINISHED_COLOR)
            elif idx == cur and active:
                item.setBackground(_ACTIVE_COLOR)
            else:
                item.setBackground(QBrush())

    def _on_step_selected(self, row):
        """Show the selected step's parameters in the editable table."""
        self._current_step = row
        self.step_table.setRowCount(0)
        if row < 0 or row >= len(self._step_entries):
            self.apply_btn.setEnabled(False)
            return
        entry = self._step_entries[row]
        if not isinstance(entry, dict):
            # Non-dict entry: show it read-only, nothing to edit.
            self._set_value_row(0, "value", entry, editable=False)
            self.step_table.setRowCount(1)
            self.apply_btn.setEnabled(False)
            return
        # '$type' first (read-only — it is the schema discriminator), then
        # the remaining parameters in definition order.
        keys = [k for k in entry if k != '$type']
        if '$type' in entry:
            keys = ['$type'] + keys
        self.step_table.setRowCount(len(keys))
        for r, key in enumerate(keys):
            self._set_value_row(r, key, entry[key], editable=(key != '$type'))
        self.apply_btn.setEnabled(True)

    def _on_apply(self):
        """Write edited table values back into the selected step entry.

        The entry is a reference into the loaded protocol, so this mutates
        the protocol the orchestrator runs (future, not-yet-executed steps
        pick up the change). Fields that fail type coercion are left
        unchanged and reported.
        """
        row = self._current_step
        if row < 0 or row >= len(self._step_entries):
            return
        entry = self._step_entries[row]
        if not isinstance(entry, dict):
            return
        errors = []
        for r in range(self.step_table.rowCount()):
            key_item = self.step_table.item(r, 0)
            value_item = self.step_table.item(r, 1)
            if key_item is None or value_item is None:
                continue
            key = key_item.text()
            if key == '$type':
                continue
            original = value_item.data(Qt.ItemDataRole.UserRole)
            try:
                entry[key] = self._coerce(value_item.text(), original)
            except (ValueError, SyntaxError) as exc:
                errors.append("{}: {}".format(key, exc))
        if errors:
            QMessageBox.warning(
                self, "Could not apply some values",
                "These fields were left unchanged:\n\n" + "\n".join(errors))
        # Re-render from the stored entry so displayed text and the cached
        # value types reflect what was actually written.
        self._on_step_selected(row)

    # --- editing helpers

    def _set_value_row(self, r, key, value, editable):
        key_item = QTableWidgetItem(str(key))
        key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.step_table.setItem(r, 0, key_item)

        value_item = QTableWidgetItem(self._format_value(value))
        # Stash the original value so _coerce knows the target type.
        value_item.setData(Qt.ItemDataRole.UserRole, value)
        if not editable:
            value_item.setFlags(
                value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.step_table.setItem(r, 1, value_item)

    @staticmethod
    def _format_value(value):
        """Render a value for the cell. Containers use a literal-parseable
        repr so :meth:`_coerce` can round-trip them via ast.literal_eval."""
        if isinstance(value, (list, dict, tuple)):
            return repr(value)
        return str(value)

    @staticmethod
    def _coerce(text, original):
        """Convert edited cell text back to the original value's type.

        Booleans accept true/false/1/0/yes/no/on/off; ints and floats parse
        directly; strings pass through; None and container types are parsed
        as Python literals. Raises ValueError/SyntaxError on bad input.
        """
        text = text.strip()
        if isinstance(original, bool):  # before int — bool subclasses int
            low = text.lower()
            if low in ('true', '1', 'yes', 'on'):
                return True
            if low in ('false', '0', 'no', 'off'):
                return False
            raise ValueError("expected a boolean")
        if isinstance(original, int):
            return int(text)
        if isinstance(original, float):
            return float(text)
        if isinstance(original, str):
            return text
        # None or container types: parse a Python literal.
        return ast.literal_eval(text)
