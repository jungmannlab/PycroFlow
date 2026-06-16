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
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QListWidget, QPlainTextEdit, QFileDialog, QGroupBox, QTableWidget,
    QTableWidgetItem, QMessageBox, QProgressBar, QAbstractItemView,
)

from PycroFlow.services.experiment_service import ExperimentState
from PycroFlow.gui.widgets.dnd import YamlDropMixin
from PycroFlow.protocols.timing import (
    estimate_durations, estimate_total_duration, estimate_remaining,
    format_duration)


# The subsystems, in display order.
_SYSTEMS = ('fluid', 'img', 'illu')
_SYSTEM_LABELS = {'fluid': 'Fluid', 'img': 'Imaging', 'illu': 'Illumination'}

# Scroll a list so the target row sits in the middle of the viewport.
_CENTER = QAbstractItemView.ScrollHint.PositionAtCenter

# States during which we poll the orchestrator for live progress.
_ACTIVE_STATES = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
                  ExperimentState.PAUSED}

# Which run controls are enabled in each experiment state. Start is also
# available after a run finished/aborted, to launch a fresh run of the same
# loaded sequence (the service rebuilds the orchestrator for it).
_CAN_START = {ExperimentState.LOADED, ExperimentState.ORCHESTRATING,
              ExperimentState.PAUSED, ExperimentState.FINISHED,
              ExperimentState.ABORTED}
_CAN_ABORT = {ExperimentState.ORCHESTRATING, ExperimentState.RUNNING,
              ExperimentState.PAUSED}
# Loading a new run sequence is only allowed when nothing is running.
_CAN_LOAD = {ExperimentState.IDLE, ExperimentState.LOADED,
             ExperimentState.FINISHED, ExperimentState.ABORTED}

# Step-list shading.
_FINISHED_COLOR = QColor("#e8f5e9")   # light green — completed
_ACTIVE_COLOR = QColor("#fff59d")     # amber — currently executing


class _Stopwatch:
    """Monotonic stopwatch that accumulates running time across pauses.

    :meth:`start` is idempotent (a second start while running is a no-op), so
    it is safe to drive from repeated state-change notifications.
    """

    def __init__(self):
        self._accum = 0.0
        self._since = None

    def reset(self):
        self._accum = 0.0
        self._since = None

    def start(self):
        if self._since is None:
            self._since = time.monotonic()

    def pause(self):
        if self._since is not None:
            self._accum += time.monotonic() - self._since
            self._since = None

    def elapsed(self):
        running = (time.monotonic() - self._since) if self._since else 0.0
        return self._accum + running


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
        # Per-step logical "time" (longest-path level over the signal/wait
        # happens-before graph), used to correlate concurrent steps across
        # systems. {system: [level per entry]}.
        self._levels = {s: [] for s in _SYSTEMS}
        # Estimated per-entry durations (seconds) per system, and their total,
        # used for the time-remaining / total-duration estimates. Refreshed
        # whenever a protocol is (re)populated.
        self._durations = {s: [] for s in _SYSTEMS}
        self._total_duration = 0.0
        # Wall-clock stopwatches for the elapsed-time readouts: one for the
        # whole run, one reset at the start of each round. Both accumulate
        # only while RUNNING (frozen on pause/finish).
        self._overall_sw = _Stopwatch()
        self._round_sw = _Stopwatch()
        self._round_index_seen = -1
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
        # A grid keeps the bars aligned: column 0 = label, column 1 = bar (the
        # only stretching column, so all bars share width and right edge),
        # column 2 = the right-aligned current/total count. The bars show only
        # the percentage; the verbose elapsed/remaining estimates live in a
        # single summary line below (in-bar text is unreliable on macOS's
        # minimal progress-bar style and made the bars' right edges ragged).
        prog_box = QGroupBox("Progress")
        prog_grid = QGridLayout(prog_box)
        prog_grid.setColumnStretch(1, 1)
        # Time summary (top, centered): estimated total before the run;
        # elapsed / remaining / total (overall and current-round) while it
        # runs. In-bar text is unreliable on macOS's minimal progress-bar
        # style and made the bars' right edges ragged, so the verbose
        # estimates live here rather than inside the bars.
        self.total_estimate_label = QLabel("")
        self.total_estimate_label.setStyleSheet("color: gray;")
        self.total_estimate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_grid.addWidget(self.total_estimate_label, 0, 0, 1, 3)
        # A grid keeps the bars aligned: column 0 = label, column 1 = bar (the
        # only stretching column, so all bars share width and right edge),
        # column 2 = the right-aligned current/total count.
        self.overall_bar, self.overall_count, _ = self._add_bar(
            prog_grid, 1, "Overall")
        # Steps performed within the round currently being executed.
        self.current_round_bar, self.current_round_count, _ = self._add_bar(
            prog_grid, 2, "Steps in Round")
        # Round counter + per-subsystem step status on one line.
        self.step_status = QLabel("—")
        self.step_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_grid.addWidget(self.step_status, 3, 0, 1, 3)
        # Per-subsystem within-step progress (e.g. imaging frames, fluid
        # incubation wait). Only meaningful for some steps, so each row is
        # hidden until its subsystem reports sub-progress.
        self.substep_bars = {}
        for i, system in enumerate(_SYSTEMS):
            bar, count, name = self._add_bar(
                prog_grid, 4 + i, _SYSTEM_LABELS[system])
            self.substep_bars[system] = (name, bar, count)
            self._set_substep_visible(system, False)
        layout.addWidget(prog_box)

        # --- per-subsystem step lists (side by side) + parameters below
        steps_box = QGroupBox("Run Sequence Steps")
        steps_layout = QVBoxLayout(steps_box)

        steps_head = QHBoxLayout()
        self.center_btn = QPushButton("Center on current step")
        self.center_btn.clicked.connect(self._center_on_current)
        steps_head.addWidget(self.center_btn)
        steps_head.addWidget(QLabel(
            "Click a step to highlight the concurrent step in the other "
            "systems."))
        steps_head.addStretch()
        steps_layout.addLayout(steps_head)

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
        """Add a labelled progress bar row; return (bar, count, name_label).

        Column 0 holds the description, column 1 the bar (shows the percent),
        column 2 a right-aligned ``current/total`` count.
        """
        name = QLabel(label)
        grid.addWidget(name, row, 0)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setFormat("%p%")
        grid.addWidget(bar, row, 1)
        count = QLabel("0/0")
        count.setMinimumWidth(70)
        count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(count, row, 2)
        return bar, count, name

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
        # Centring on the current step only makes sense while one is running.
        self.center_btn.setEnabled(state in _ACTIVE_STATES)
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
        # Drive the elapsed-time stopwatches: they run only while steps
        # actually execute (RUNNING), and freeze on pause/finish/abort.
        if new is ExperimentState.RUNNING:
            self._overall_sw.start()
            self._round_sw.start()
        else:
            self._overall_sw.pause()
            self._round_sw.pause()
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
        self._levels = self._compute_levels()
        self._durations = estimate_durations(protocol)
        self._total_duration = estimate_total_duration(protocol)
        self._overall_sw.reset()
        self._round_sw.reset()
        self._round_index_seen = -1
        self._update_total_estimate_label()
        self._current_sys = None
        self._current_row = -1
        self.step_table.setRowCount(0)
        self.step_param_label.setText("Select a step above to view it.")
        self.apply_btn.setEnabled(False)

    def _update_total_estimate_label(self):
        """Show the estimated total run time (or clear it when unknown)."""
        if self._total_duration > 0:
            self.total_estimate_label.setText(
                "Estimated sequence duration: ~{}".format(
                    format_duration(self._total_duration)))
        else:
            self.total_estimate_label.setText("")

    def _update_time_summary(self, overall_remaining, round_remaining):
        """Refresh the single elapsed / remaining / total time line.

        Before the run (or with no timing estimate) it shows just the total
        estimate; once running it shows elapsed, remaining and total for the
        whole run, plus elapsed / remaining for the current round.
        """
        elapsed = self._overall_sw.elapsed()
        if self._total_duration <= 0 or elapsed <= 0:
            self._update_total_estimate_label()
            return
        txt = "Overall: {} elapsed · ~{} left · ~{} total".format(
            format_duration(elapsed), format_duration(overall_remaining),
            format_duration(self._total_duration))
        round_elapsed = self._round_sw.elapsed()
        if round_remaining > 0 or round_elapsed > 0:
            txt += "        Round: {} elapsed · ~{} left".format(
                format_duration(round_elapsed),
                format_duration(round_remaining))
        self.total_estimate_label.setText(txt)

    def _compute_levels(self):
        """Assign each step a logical 'time' for cross-system correlation.

        Builds the happens-before graph from program order (a step follows the
        previous one in its system) plus signal -> wait edges (a
        ``wait for signal`` follows the ``signal`` that emits its value), then
        takes the longest-path level of each node. Steps with the same level
        run concurrently; per system the level is strictly increasing, so the
        step a system is in at level ``L`` is its last step with level <= L.
        """
        # value -> (system, index) of the signal that emits it.
        sigmap = {}
        for system in _SYSTEMS:
            for i, e in enumerate(self._entries[system]):
                if isinstance(e, dict) and e.get('$type') == 'signal':
                    val = e.get('value')
                    if val is not None and val not in sigmap:
                        sigmap[val] = (system, i)
        levels = {s: [0] * len(self._entries[s]) for s in _SYSTEMS}
        total = sum(len(self._entries[s]) for s in _SYSTEMS)
        # Longest-path relaxation; converges in <= (longest chain) passes,
        # bounded by the node count.
        for _ in range(total + 1):
            changed = False
            for system in _SYSTEMS:
                entries = self._entries[system]
                for i, e in enumerate(entries):
                    lvl = levels[system][i - 1] + 1 if i > 0 else 0
                    if (isinstance(e, dict)
                            and e.get('$type') == 'wait for signal'):
                        dep = sigmap.get(e.get('value'))
                        if dep is not None:
                            ds, di = dep
                            lvl = max(lvl, levels[ds][di] + 1)
                    if lvl != levels[system][i]:
                        levels[system][i] = lvl
                        changed = True
            if not changed:
                break
        return levels

    def _concurrent_indices(self, system, idx):
        """For each *other* system, the step it runs during ``system``[idx].

        Returns {other_system: index}. Uses the logical levels: per system a
        step occupies the interval from just after the previous step up to its
        own level (a blocked ``wait`` spans until its signal arrives, so its
        level is high). So the step a system is in at level ``L`` is the first
        one whose level >= L (levels are strictly increasing per system); if
        ``L`` is past the system's last step, it has finished — show the last.
        """
        out = {}
        levs = self._levels.get(system) or []
        if idx >= len(levs):
            return out
        target = levs[idx]
        for other in _SYSTEMS:
            if other == system:
                continue
            other_levs = self._levels.get(other) or []
            if not other_levs:
                continue
            best = len(other_levs) - 1
            for j, lv in enumerate(other_levs):
                if lv >= target:
                    best = j
                    break
            out[other] = best
        return out

    def _center_on_current(self):
        """Scroll all three lists so the current step sits in the centre."""
        prog = self._service.progress()
        if not prog:
            return
        for system in _SYSTEMS:
            lst = self.step_lists[system]
            if not lst.count():
                continue
            cur = prog.get(system, (0, 0))[0]
            cur = min(max(cur, 0), lst.count() - 1)
            lst.scrollToItem(lst.item(cur), _CENTER)

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

        done_rounds, total_rounds = self._round_counts(prog.get('img'))
        parts = []
        if total_rounds:
            # 1-based number of the round currently executing.
            current_round = min(done_rounds + 1, total_rounds)
            parts.append("Round {}/{}".format(current_round, total_rounds))
        for key in _SYSTEMS:
            if key in prog:
                cur, tot = prog[key]
                parts.append(
                    "{} {}/{} ({})".format(
                        key, cur, tot, self._step_name(key, cur)))
        self.step_status.setText("      ".join(parts))

        round_remaining = self._update_current_round_bar(
            prog, done_rounds, total_rounds)
        self._update_time_summary(
            estimate_remaining(self._durations, prog), round_remaining)
        self._update_substep_bars()
        self._shade_steps(prog)
        self._check_finished()

    def _check_finished(self):
        """Leave the running state once every subsystem has finished.

        The orchestrator runs to completion on its own threads but does not
        announce it; we detect it here (the poll already runs while RUNNING)
        and call ``end()``, which transitions to FINISHED. That re-enables the
        run controls and unlocks the hardware tabs via the usual state-change
        handlers.
        """
        if (self._service.state is ExperimentState.RUNNING
                and self._service.is_finished()):
            self._service.end()

    def _set_substep_visible(self, system, visible):
        for w in self.substep_bars[system]:
            w.setVisible(visible)

    def _update_substep_bars(self):
        """Update the per-subsystem within-step bars (hidden when N/A)."""
        getter = getattr(self._service, 'step_progress', None)
        sp = getter() if callable(getter) else {}
        if not isinstance(sp, dict):
            sp = {}
        for system in _SYSTEMS:
            prog = sp.get(system)
            if not (isinstance(prog, (tuple, list)) and len(prog) == 3):
                self._set_substep_visible(system, False)
                continue
            cur, tot, name = prog
            _, bar, count = self.substep_bars[system]
            bar.setValue(int(100 * cur / tot) if tot else 0)
            count.setText(self._substep_caption(name, cur, tot))
            self._set_substep_visible(system, True)

    @staticmethod
    def _substep_caption(name, cur, tot):
        # Imaging counts frames; the fluid steps (incubate / inject /
        # pump_out) are time-based and shown in seconds.
        if name == 'frames':
            return "frames {}/{}".format(int(cur), int(tot))
        return "{} {:.0f}/{:.0f} s".format(name, cur, tot)

    def _step_name(self, system, cur):
        """``$type`` of the step a subsystem is currently on (or 'done')."""
        entries = self._entries.get(system, [])
        if 0 <= cur < len(entries) and isinstance(entries[cur], dict):
            return entries[cur].get('$type', '?')
        if entries and cur >= len(entries):
            return "done"
        return "—"

    def _round_counts(self, img_prog):
        """(completed_rounds, total_rounds) from the imaging acquisitions.

        Each ``acquire`` step is one round; the number already passed by the
        imaging handler gives the completed-round count.
        """
        protocol = self._service.protocol or {}
        img = protocol.get('img', {})
        entries = (
            img.get('protocol_entries', []) if isinstance(img, dict) else [])
        acquire_idx = [
            i for i, e in enumerate(entries)
            if isinstance(e, dict) and e.get('$type') == 'acquire']
        total_rounds = len(acquire_idx)
        if not total_rounds:
            return 0, 0
        cur = img_prog[0] if img_prog else 0
        done_rounds = sum(1 for i in acquire_idx if i < cur)
        return done_rounds, total_rounds

    def _update_current_round_bar(self, prog, done_rounds, total_rounds):
        """Show step progress within the round currently being executed.

        Counts, across all subsystems, the steps belonging to the in-progress
        round (index ``done_rounds``) and how many of those are done. Returns
        the estimated seconds of work left in the round (for the time line).
        """
        if not total_rounds:
            self.current_round_bar.setValue(0)
            self.current_round_count.setText("—")
            return 0.0
        current = min(done_rounds, total_rounds)
        # Restart the round stopwatch whenever execution moves to a new round,
        # so its elapsed reading is time spent in the *current* round.
        if current != self._round_index_seen:
            self._round_index_seen = current
            self._round_sw.reset()
            if self._service.state is ExperimentState.RUNNING:
                self._round_sw.start()
        done = total = 0
        remaining = 0.0
        for system in _SYSTEMS:
            cur = prog.get(system, (0, 0))[0]
            durs = self._durations.get(system, [])
            for idx, rnd in enumerate(self._round_of.get(system, [])):
                if rnd == current:
                    total += 1
                    if idx < cur:
                        done += 1
                    elif idx < len(durs):
                        remaining += durs[idx]
        pct = int(100 * done / total) if total else 0
        self.current_round_bar.setValue(pct)
        self.current_round_count.setText("{}/{}".format(done, total))
        return remaining

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
        """Show the clicked step's parameters and correlate the other lists.

        Three lists feed one parameter box (which shows the *last* clicked
        step). Clicking also selects + centres, in the other two systems, the
        step they will be in while the clicked step runs (traced via the
        signal / wait-for-signal happens-before graph).
        """
        if row < 0:
            # Deselection — e.g. from clearing another list, or repopulating.
            return
        corr = self._concurrent_indices(system, row)
        for other, lst in self.step_lists.items():
            if other == system:
                continue
            lst.blockSignals(True)
            j = corr.get(other)
            if j is not None and 0 <= j < lst.count():
                lst.setCurrentRow(j)
                lst.scrollToItem(lst.item(j), _CENTER)
            else:
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
