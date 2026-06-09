"""Experiment tab: load a protocol, run it, watch state and log output.

Drives :class:`PycroFlow.services.experiment_service.ExperimentService` and
subscribes to a :class:`PycroFlow.gui.qt_bridge.QtBridge` for thread-safe
state/log updates.
"""
import ast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget,
    QPlainTextEdit, QFileDialog, QGroupBox, QTableWidget, QTableWidgetItem,
    QMessageBox,
)

from PycroFlow.services.experiment_service import ExperimentState
from PycroFlow.gui.widgets.dnd import YamlDropMixin


# Which controls are enabled in each state.
_CAN_START = {ExperimentState.LOADED, ExperimentState.ORCHESTRATING,
              ExperimentState.PAUSED}
_CAN_PAUSE = {ExperimentState.RUNNING}
_CAN_RESUME = {ExperimentState.PAUSED}
_CAN_ABORT = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
              ExperimentState.PAUSED}


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
        self._current_step = -1
        self._build_ui()
        self._connect_signals()
        self._refresh_controls(self._service.state)

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

        # --- protocol controls
        controls = QHBoxLayout()
        self.load_btn = QPushButton("Load protocol…")
        self.start_btn = QPushButton("Start")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.abort_btn = QPushButton("Abort")
        for b in (self.load_btn, self.start_btn, self.pause_btn,
                  self.resume_btn, self.abort_btn):
            controls.addWidget(b)
        controls.addStretch()
        layout.addLayout(controls)

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
        self.load_btn.clicked.connect(self._on_load)
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.resume_btn.clicked.connect(self._on_resume)
        self.abort_btn.clicked.connect(self._on_abort)
        self._bridge.state_changed.connect(self._on_state_changed)
        self._bridge.log_message.connect(self._on_log)
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        self.apply_btn.clicked.connect(self._on_apply)

    # --- service-driven commands

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load protocol YAML", "", "YAML files (*.yaml *.yml)")
        if not path:
            return
        self._service.load_protocol_from_yaml(path)
        self._populate_steps()

    def load_protocol_path(self, path):
        """Programmatic load (used by the toolbar / tests)."""
        self._service.load_protocol_from_yaml(path)
        self._populate_steps()

    def on_yaml_dropped(self, path):
        """Load a Run Sequence YAML dropped onto the tab."""
        self.load_protocol_path(path)

    def _on_start(self):
        self._service.start()

    def _on_pause(self):
        self._service.pause()

    def _on_resume(self):
        self._service.resume()

    def _on_abort(self):
        self._service.abort()

    # --- bridge-driven UI updates (run on the GUI thread)

    def _on_state_changed(self, old, new):
        self.state_label.setText(new.value)
        self._refresh_controls(new)
        # Repopulate the step list whenever a protocol becomes loaded,
        # regardless of how it was loaded (button, toolbar, or programmatic),
        # so the view always reflects the active protocol.
        if new is ExperimentState.LOADED:
            self._populate_steps()

    def _on_log(self, message):
        self.log_view.appendPlainText(message)

    # --- helpers

    def _refresh_controls(self, state):
        self.start_btn.setEnabled(state in _CAN_START)
        self.pause_btn.setEnabled(state in _CAN_PAUSE)
        self.resume_btn.setEnabled(state in _CAN_RESUME)
        self.abort_btn.setEnabled(state in _CAN_ABORT)

    def _populate_steps(self):
        self.step_list.clear()
        self._step_entries = []
        protocol = self._service.protocol or {}
        # List each subsystem's steps, prefixed with the subsystem so the
        # combined view is unambiguous. _step_entries stays aligned with the
        # list rows so a selection can show the full entry.
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
        self._current_step = -1
        self.step_table.setRowCount(0)
        self.apply_btn.setEnabled(False)

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
