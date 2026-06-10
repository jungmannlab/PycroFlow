"""Run Sequence tab: watch a loaded protocol run — state, progress, log.

Drives :class:`PycroFlow.services.experiment_service.ExperimentService` and
subscribes to a :class:`PycroFlow.gui.qt_bridge.QtBridge` for thread-safe
state/log updates. Owns the run controls — Load run sequence, Start, a
Pause/Resume toggle, and Abort — enabled/relabelled per experiment state.

The three subsystems (fluid / img / illu) run in parallel, so their steps are
shown in three side-by-side lists. Clicking a step in any list shows that
step's parameters in the editable box below, labelled with which list it came
from.
"""
import ast

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QListWidget, QPlainTextEdit, QFileDialog, QGroupBox, QTableWidget,
    QTableWidgetItem, QMessageBox, QProgressBar,
)

from PycroFlow.services.experiment_service import ExperimentState
from PycroFlow.gui.widgets.dnd import YamlDropMixin


# The subsystems, in display order.
_SYSTEMS = ('fluid', 'img', 'illu')
_SYSTEM_LABELS = {'fluid': 'Fluid', 'img': 'Imaging', 'illu': 'Illumination'}

# States during which we poll the orchestrator for live progress.
_ACTIVE_STATES = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
                  ExperimentState.PAUSED}

# Which run controls are enabled in each experiment state.
_CAN_START = {ExperimentState.LOADED, ExperimentState.ORCHESTRATING,
              ExperimentState.PAUSED}
_CAN_ABORT = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
              ExperimentState.PAUSED}
# Loading a new run sequence is only allowed when nothing is running.
_CAN_LOAD = {ExperimentState.IDLE, ExperimentState.LOADED,
             ExperimentState.FINISHED, ExperimentState.ABORTED}

# Step-list shading.
_FINISHED_COLOR = QColor("#e8f5e9")   # light green — completed
_ACTIVE_COLOR = QColor("#fff59d")     # amber — currently executing


class ExperimentTab(YamlDropMixin, QWidget):
    def __init__(self, service, bridge, parent=None):
        super().__init__(parent)
        self._service = service
        self._bridge = bridge
        self.enable_yaml_drop()
        # Per-subsystem backing store: the entry dicts shown in each list, and
        # the round index of each entry (for the current-round bar). Entries
        # are references into the loaded protocol, so editing them in the table
        # mutates the protocol the orchestrator runs.
        self._entries = {s: [] for s in _SYSTEMS}
        self._round_of = {s: [] for s in _SYSTEMS}
        # The step whose parameters are shown (the last one clicked).
        self._current_sys = None
        self._current_row = -1
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
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

        # --- run controls
        controls = QHBoxLayout()
        self.load_btn = QPushButton("Load run sequence…")
        self.start_btn = QPushButton("Start")
        # One button toggles Pause/Resume depending on the run state.
        self.pause_resume_btn = QPushButton("Pause")
        self.abort_btn = QPushButton("Abort")
        for b in (self.load_btn, self.start_btn, self.pause_resume_btn,
                  self.abort_btn):
            controls.addWidget(b)
        controls.addStretch()
        layout.addLayout(controls)

        # --- progress
        # A grid keeps the three bars aligned: column 0 = label, column 1 =
        # bar (the only stretching column, so all bars are the same width),
        # column 2 = the right-aligned current/total count.
        prog_box = QGroupBox("Progress")
        prog_grid = QGridLayout(prog_box)
        prog_grid.setColumnStretch(1, 1)
        self.overall_bar, self.overall_count = self._add_bar(
            prog_grid, 0, "Overall")
        self.round_bar, self.round_count = self._add_bar(
            prog_grid, 1, "Rounds in Experiment")
        # Steps performed within the round currently being executed.
        self.current_round_bar, self.current_round_count = self._add_bar(
            prog_grid, 2, "Steps in Round")
        self.step_status = QLabel("—")
        self.step_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_grid.addWidget(self.step_status, 3, 0, 1, 3)
        layout.addWidget(prog_box)

        # --- per-subsystem step lists (side by side) + parameters below
        steps_box = QGroupBox("Protocol steps")
        steps_layout = QVBoxLayout(steps_box)

        lists_row = QHBoxLayout()
        self.step_lists = {}
        for system in _SYSTEMS:
            col = QVBoxLayout()
            col.addWidget(QLabel(_SYSTEM_LABELS[system]))
            lst = QListWidget()
            self.step_lists[system] = lst
            col.addWidget(lst)
            lists_row.addLayout(col, 1)
        steps_layout.addLayout(lists_row, 1)

        params_box = QGroupBox("Step parameters (editable)")
        params_layout = QVBoxLayout(params_box)
        self.step_param_label = QLabel("Select a step above to view it.")
        params_layout.addWidget(self.step_param_label)
        self.step_table = QTableWidget(0, 2)
        self.step_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.step_table.horizontalHeader().setStretchLastSection(True)
        self.step_table.verticalHeader().setVisible(False)
        params_layout.addWidget(self.step_table)
        self.apply_btn = QPushButton("Apply changes")
        self.apply_btn.setEnabled(False)
        params_layout.addWidget(self.apply_btn)
        steps_layout.addWidget(params_box)

        layout.addWidget(steps_box, 1)

        # --- log pane (compact — it carries little detail)
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box)

    @staticmethod
    def _add_bar(grid, row, label):
        """Add a labelled progress bar row to a grid; return (bar, count).

        Column 0 holds the description, column 1 the bar (shows the percent),
        column 2 a right-aligned ``current/total`` count.
        """
        grid.addWidget(QLabel(label), row, 0)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setFormat("%p%")
        grid.addWidget(bar, row, 1)
        count = QLabel("0/0")
        count.setMinimumWidth(70)
        count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(count, row, 2)
        return bar, count

    def _connect_signals(self):
        self._bridge.state_changed.connect(self._on_state_changed)
        self._bridge.log_message.connect(self._on_log)
        for system, lst in self.step_lists.items():
            lst.currentRowChanged.connect(
                lambda row, s=system: self._on_step_selected(s, row))
        self.apply_btn.clicked.connect(self._on_apply)
        self._poll_timer.timeout.connect(self._poll_progress)
        self.load_btn.clicked.connect(self._on_load)
        self.start_btn.clicked.connect(self._on_start)
        self.pause_resume_btn.clicked.connect(self._on_pause_resume)
        self.abort_btn.clicked.connect(self._on_abort)

    # --- run controls

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Run Sequence YAML", "", "YAML files (*.yaml *.yml)")
        if path:
            self.load_protocol_path(path)

    def _on_start(self):
        self._service.start()

    def _on_pause_resume(self):
        if self._service.state is ExperimentState.RUNNING:
            self._service.pause()
        elif self._service.state is ExperimentState.PAUSED:
            self._service.resume()

    def _on_abort(self):
        self._service.abort()

    def load_protocol_path(self, path):
        """Programmatic load (used by the toolbar / drag&drop / tests)."""
        self._service.load_protocol_from_yaml(path)
        self._populate_steps()

    def on_yaml_dropped(self, path):
        """Load a Run Sequence YAML dropped onto the tab."""
        self.load_protocol_path(path)

    def _refresh_controls(self, state):
        """Enable/disable + relabel the run controls for the given state."""
        self.load_btn.setEnabled(state in _CAN_LOAD)
        self.start_btn.setEnabled(state in _CAN_START)
        self.abort_btn.setEnabled(state in _CAN_ABORT)
        if state is ExperimentState.RUNNING:
            self.pause_resume_btn.setText("Pause")
            self.pause_resume_btn.setEnabled(True)
        elif state is ExperimentState.PAUSED:
            self.pause_resume_btn.setText("Resume")
            self.pause_resume_btn.setEnabled(True)
        else:
            self.pause_resume_btn.setText("Pause")
            self.pause_resume_btn.setEnabled(False)

    # --- bridge-driven UI updates (run on the GUI thread)

    def _on_state_changed(self, old, new):
        self.state_label.setText(new.value)
        self._refresh_controls(new)
        # Repopulate the step lists whenever a protocol becomes loaded,
        # regardless of how it was loaded (toolbar, drag&drop, programmatic),
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
        protocol = self._service.protocol or {}
        for system in _SYSTEMS:
            lst = self.step_lists[system]
            lst.clear()
            sub = protocol.get(system, {})
            entries = (
                sub.get('protocol_entries', []) if isinstance(sub, dict)
                else [])
            self._entries[system] = list(entries)
            self._round_of[system] = self._round_indices(entries)
            for i, entry in enumerate(entries):
                type_ = (
                    entry.get('$type', '?') if isinstance(entry, dict)
                    else '?')
                lst.addItem("{:d}: {}".format(i, type_))
        self._current_sys = None
        self._current_row = -1
        self.step_table.setRowCount(0)
        self.step_param_label.setText("Select a step above to view it.")
        self.apply_btn.setEnabled(False)

    @staticmethod
    def _is_round_marker(entry):
        """Whether an entry marks the end of a round for its subsystem.

        Each PycroFlow round is one imaging acquisition; subsystems sync on it.
        So a round closes at an ``acquire`` (imaging) or at the
        ``wait for signal`` on the imaging subsystem (fluid/illumination wait
        for imaging to finish before the next round). Both occur exactly once
        per round, giving a consistent round count across subsystems.
        """
        if not isinstance(entry, dict):
            return False
        type_ = entry.get('$type')
        if type_ == 'acquire':
            return True
        if type_ == 'wait for signal' and entry.get('target') == 'img':
            return True
        return False

    @classmethod
    def _round_indices(cls, entries):
        """Return the round index of each entry (markers before it)."""
        out = []
        seen = 0
        for entry in entries:
            out.append(seen)
            if cls._is_round_marker(entry):
                seen += 1
        return out

    # --- live progress

    def _poll_progress(self):
        prog = self._service.progress()
        if not prog:
            return
        done = sum(c for c, _ in prog.values())
        total = sum(t for _, t in prog.values())
        pct = int(100 * done / total) if total else 0
        self.overall_bar.setValue(pct)
        self.overall_count.setText("{}/{}".format(done, total))

        parts = []
        for key in _SYSTEMS:
            if key in prog:
                cur, tot = prog[key]
                parts.append(
                    "{} {}/{} ({})".format(
                        key, cur, tot, self._step_name(key, cur)))
        self.step_status.setText("      ".join(parts))

        done_rounds, total_rounds = self._update_round_bar(prog.get('img'))
        self._update_current_round_bar(prog, done_rounds, total_rounds)
        self._shade_steps(prog)

    def _step_name(self, system, cur):
        """``$type`` of the step a subsystem is currently on (or 'done')."""
        entries = self._entries.get(system, [])
        if 0 <= cur < len(entries) and isinstance(entries[cur], dict):
            return entries[cur].get('$type', '?')
        if entries and cur >= len(entries):
            return "done"
        return "—"

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
            self.round_count.setText("—")
            return 0, 0
        cur = img_prog[0] if img_prog else 0
        done_rounds = sum(1 for i in acquire_idx if i < cur)
        self.round_bar.setValue(int(100 * done_rounds / total_rounds))
        self.round_count.setText("{}/{}".format(done_rounds, total_rounds))
        return done_rounds, total_rounds

    def _update_current_round_bar(self, prog, done_rounds, total_rounds):
        """Show step progress within the round currently being executed.

        Counts, across all subsystems, the steps belonging to the in-progress
        round (index ``done_rounds``) and how many of those are done.
        """
        if not total_rounds:
            self.current_round_bar.setValue(0)
            self.current_round_count.setText("—")
            return
        current = min(done_rounds, total_rounds)
        done = total = 0
        for system in _SYSTEMS:
            cur = prog.get(system, (0, 0))[0]
            for idx, rnd in enumerate(self._round_of.get(system, [])):
                if rnd == current:
                    total += 1
                    if idx < cur:
                        done += 1
        pct = int(100 * done / total) if total else 0
        self.current_round_bar.setValue(pct)
        self.current_round_count.setText("{}/{}".format(done, total))

    def _shade_steps(self, prog):
        active = self._service.state in _ACTIVE_STATES
        for system in _SYSTEMS:
            lst = self.step_lists[system]
            cur, _ = prog.get(system, (0, 0))
            for idx in range(lst.count()):
                item = lst.item(idx)
                if item is None:
                    continue
                if idx < cur:
                    item.setBackground(_FINISHED_COLOR)
                elif idx == cur and active:
                    item.setBackground(_ACTIVE_COLOR)
                else:
                    item.setBackground(QBrush())

    def _on_step_selected(self, system, row):
        """Show the clicked step's parameters in the editable table.

        Three lists feed one parameter box, so clicking in one clears the
        others' selection (the box shows the *last* clicked step).
        """
        if row < 0:
            # Deselection — e.g. from clearing another list, or repopulating.
            return
        for other, lst in self.step_lists.items():
            if other != system:
                lst.blockSignals(True)
                lst.setCurrentRow(-1)
                lst.blockSignals(False)

        self._current_sys = system
        self._current_row = row
        self.step_table.setRowCount(0)
        entries = self._entries.get(system, [])
        if row >= len(entries):
            self.apply_btn.setEnabled(False)
            return
        entry = entries[row]
        type_ = entry.get('$type', '?') if isinstance(entry, dict) else '?'
        self.step_param_label.setText(
            "{} · step {}: {}".format(_SYSTEM_LABELS[system], row, type_))
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
        entries = self._entries.get(self._current_sys, [])
        row = self._current_row
        if row < 0 or row >= len(entries):
            return
        entry = entries[row]
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
        self._on_step_selected(self._current_sys, row)

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
