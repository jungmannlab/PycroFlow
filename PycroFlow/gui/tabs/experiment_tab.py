"""Experiment tab: load a protocol, run it, watch state and log output.

Drives :class:`PycroFlow.services.experiment_service.ExperimentService` and
subscribes to a :class:`PycroFlow.gui.qt_bridge.QtBridge` for thread-safe
state/log updates.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget,
    QPlainTextEdit, QFileDialog, QGroupBox,
)

from PycroFlow.services.experiment_service import ExperimentState


# Which controls are enabled in each state.
_CAN_START = {ExperimentState.LOADED, ExperimentState.ORCHESTRATING,
              ExperimentState.PAUSED}
_CAN_PAUSE = {ExperimentState.RUNNING}
_CAN_RESUME = {ExperimentState.PAUSED}
_CAN_ABORT = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
              ExperimentState.PAUSED}


class ExperimentTab(QWidget):
    def __init__(self, service, bridge, parent=None):
        super().__init__(parent)
        self._service = service
        self._bridge = bridge
        # Backing store for the step list so a selection can look up the
        # full entry dict (the list only shows index + $type).
        self._step_entries = []
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

        params_box = QGroupBox("Step parameters")
        params_layout = QVBoxLayout(params_box)
        self.step_params = QPlainTextEdit()
        self.step_params.setReadOnly(True)
        self.step_params.setPlaceholderText(
            "Select a step to see its parameters.")
        params_layout.addWidget(self.step_params)
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
        self.step_params.clear()

    def _on_step_selected(self, row):
        """Show the selected step's parameters in the side box."""
        if row < 0 or row >= len(self._step_entries):
            self.step_params.clear()
            return
        entry = self._step_entries[row]
        if not isinstance(entry, dict):
            self.step_params.setPlainText(str(entry))
            return
        # '$type' first, then the remaining parameters in definition order.
        lines = []
        if '$type' in entry:
            lines.append("$type: {}".format(entry['$type']))
        for key, value in entry.items():
            if key == '$type':
                continue
            lines.append("{}: {}".format(key, value))
        self.step_params.setPlainText("\n".join(lines))
