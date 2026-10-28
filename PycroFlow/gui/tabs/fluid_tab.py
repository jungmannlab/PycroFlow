"""Fluid tab: tubing ops, manual control, and an emergency stop.

Sections:
* **Tubing operations** — fill / clean tubings.
* **Manual control** — a dropdown picks one of three pump operations (a single
  pump stroke, a pump move between an input and an output reservoir, or a full
  fluid move: the protocol's simultaneous inject + extract), with only the
  selected operation's controls shown; plus direct valve routing.
* **STOP ALL MOVES** — a standalone emergency button.

All commands go through
:class:`PycroFlow.services.system_service.SystemService` so the tab never
reaches into private attributes of the fluid system.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QGroupBox,
    QFormLayout,
    QComboBox,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStackedWidget,
)

from PycroFlow.gui.widgets.worker import run_in_background
from PycroFlow.gui.widgets.fluid_schematic import FluidSchematic

#: How often (ms) the live schematic re-reads cached valve/syringe state. The
#: read issues no serial traffic, so this can stay brisk during a run.
_SCHEMATIC_POLL_MS = 300

_STOP_STYLE = (
    "background-color: #b00000; color: white; font-weight: bold; "
    "padding: 8px;"
)


class FluidTab(QWidget):
    def __init__(self, system_service, on_connect=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._on_connect = on_connect
        self._busy = False
        # Manual schematic toggles are blocked while the orchestrator runs.
        self._run_locked = False
        self._build_ui()
        # Buttons disabled while a fluid op runs in the background (the serial
        # bus serves one operation at a time). STOP stays enabled.
        self._busy_buttons = [
            self.fill_btn,
            self.clean_btn,
            self.stroke_btn,
            self.move_btn,
            self.full_btn,
            self.valve_btn,
            self.open_valves_btn,
            self.close_valves_btn,
        ]

    def _build_ui(self):
        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # --- left: the controls column (scrollable so the schematic keeps
        # its width on small windows).
        controls = QWidget()
        layout = QVBoxLayout(controls)

        # --- status + connect
        status_box = QGroupBox("Fluid system")
        status_row = QHBoxLayout(status_box)
        connected = self._svc.fluid_system is not None
        self.status_label = QLabel(
            "connected" if connected else "not connected"
        )
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        status_row.addWidget(self.connect_btn)
        layout.addWidget(status_box)

        layout.addWidget(self._build_tubing_group())
        layout.addWidget(self._build_manual_group())

        # --- emergency stop (standalone)
        self.stop_btn = QPushButton("STOP ALL MOVES")
        self.stop_btn.setStyleSheet(_STOP_STYLE)
        self.stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self.stop_btn)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        splitter.addWidget(scroll)

        # --- right: live wiring / valve-state schematic.
        schematic_box = QGroupBox("Live wiring && valve state")
        sbl = QVBoxLayout(schematic_box)
        self.schematic = FluidSchematic()
        # Clicking a port toggles that ibidi channel; clicking a pump toggles
        # its syringe valve — both raw, ignoring reservoir routing.
        self.schematic.channel_clicked.connect(self._on_channel_clicked)
        self.schematic.pump_clicked.connect(self._on_pump_clicked)
        sbl.addWidget(self.schematic)
        sbl.addWidget(
            self._hint(
                "Click a port to toggle that ibidi channel open/closed; click a "
                "pump to flip its valve (in ↔ out). Raw overrides — routing is "
                "ignored."
            )
        )
        splitter.addWidget(schematic_box)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 560])

        # Poll cached valve/syringe state (no serial I/O) so the schematic
        # stays live, including while the orchestrator owns the bus.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_SCHEMATIC_POLL_MS)
        self._poll_timer.timeout.connect(self._refresh_schematic_state)
        self._poll_timer.start()
        self._reload_topology()
        # Apply the initial reservoir selection's highlight now the schematic
        # exists (during control construction it did not yet).
        self._update_route_hint()

    @staticmethod
    def _hint(text):
        """A small grey explanatory line to sit under a group's controls."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: gray;")
        return label

    def _build_tubing_group(self):
        box = QGroupBox("Tubing operations")
        outer = QVBoxLayout(box)
        row = QHBoxLayout()
        self.fill_btn = QPushButton("Fill tubings")
        self.clean_btn = QPushButton("Clean tubings")
        self.fill_btn.setToolTip(
            "Prime every tubing segment of the experiment's reservoirs, so "
            "no air or old liquid is left between reservoir and sample."
        )
        self.clean_btn.setToolTip(
            "Run the cleaning procedure through the design's cleaning "
            "reservoirs. Needles must sit in the same container first."
        )
        row.addWidget(self.fill_btn)
        row.addWidget(self.clean_btn)
        row.addStretch()
        outer.addLayout(row)
        outer.addWidget(
            self._hint(
                "Whole-system procedures over the reservoirs the loaded "
                "experiment design uses — not the full setup manifold."
            )
        )
        # Advisory estimate of the cleaning liquid the design would need.
        self.clean_volume_hint = self._hint("")
        outer.addWidget(self.clean_volume_hint)
        self.fill_btn.clicked.connect(self._on_fill)
        self.clean_btn.clicked.connect(self._on_clean)
        self._refresh_clean_estimate()
        return box

    def _refresh_clean_estimate(self):
        """Update the advisory 'cleaning liquid required' hint."""
        hint = getattr(self, "clean_volume_hint", None)
        if hint is None:
            return
        try:
            vol = self._svc.estimate_cleaning_volume()
        except Exception:  # pragma: no cover - estimate is best-effort
            vol = None
        if isinstance(vol, (int, float)) and not isinstance(vol, bool):
            hint.setText(
                "Estimated cleaning liquid required: ~{:.0f} µl "
                "(per cleaning tank, advisory).".format(vol)
            )
        else:
            hint.setText("")

    def _build_manual_group(self):
        box = QGroupBox("Manual control")
        outer = QVBoxLayout(box)

        # Operation selector: the three pump operations share one slot via a
        # stack, so only the selected operation's controls show. Keeps the tab
        # uncluttered — picking "Full fluid move" hides the stroke/move forms.
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Operation"))
        self.mode_select = QComboBox()
        self.mode_select.addItems(
            [
                "Single pump stroke",
                "Pump move (input → output reservoir)",
                "Full fluid move (inject + extract)",
            ]
        )
        self.mode_select.setToolTip(
            "Which manual pump operation to show: a single syringe stroke, a "
            "pump move between two reservoirs, or a full fluid move (pump_a "
            "injects from a reservoir while pump_out extracts to waste — the "
            "same two-pump exchange a protocol 'inject' step performs)."
        )
        mode_row.addWidget(self.mode_select)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_stroke_panel())
        self.mode_stack.addWidget(self._build_move_panel())
        self.mode_stack.addWidget(self._build_full_move_panel())
        self.mode_select.currentIndexChanged.connect(
            self.mode_stack.setCurrentIndex
        )
        outer.addWidget(self.mode_stack)

        # Valve routing stays visible for every mode: the pump operations
        # above draw from / push to wherever the valves currently point.
        outer.addWidget(self._build_valve_group())
        self._refresh_reservoirs()
        return box

    def _build_stroke_panel(self):
        # Single pump stroke — one syringe movement (draw OR expel), not a
        # full pickup+dispense cycle.
        stroke = QGroupBox("Single pump stroke")
        sform = QFormLayout(stroke)
        self.stroke_pump = QComboBox()
        self.stroke_pump.addItems(["pump_a", "pump_out"])
        self.stroke_vol = QLineEdit("100")
        self.stroke_vel = QLineEdit()
        self.stroke_vel.setPlaceholderText("default")
        # Action is the single movement; port is which syringe valve side it
        # moves through. Stored (label -> action) so the handler stays terse.
        self.stroke_action = QComboBox()
        self.stroke_action.addItem("Draw (pickup)", "pickup")
        self.stroke_action.addItem("Expel (dispense)", "dispense")
        self.stroke_port = QComboBox()
        self.stroke_port.addItems(["in", "out"])
        self.stroke_pump.setToolTip(
            "pump_a drives liquid from a reservoir to the sample; "
            "pump_out is the extraction (waste) pump."
        )
        self.stroke_vol.setToolTip(
            "Volume for the single stroke. Must not exceed the syringe "
            "capacity — one stroke is one syringe movement, never split."
        )
        self.stroke_vel.setToolTip(
            "Blank uses the design's max_velocity. The pump rejects values "
            "outside its own range — check errors.log if nothing moves."
        )
        self.stroke_action.setToolTip(
            "A single syringe movement: 'Draw' aspirates into the syringe, "
            "'Expel' dispenses out of it. One stroke only — not a round trip."
        )
        self.stroke_port.setToolTip(
            "Which syringe valve side the stroke moves through: 'in' = the "
            "reservoir side (as currently routed), 'out' = the sample/waste "
            "side."
        )
        sform.addRow("Pump", self.stroke_pump)
        sform.addRow("Volume (µL)", self.stroke_vol)
        sform.addRow("Velocity (µL/min)", self.stroke_vel)
        sform.addRow("Action", self.stroke_action)
        sform.addRow("Port", self.stroke_port)
        self.stroke_btn = QPushButton("Run stroke")
        sform.addRow(self.stroke_btn)
        sform.addRow(
            self._hint(
                "One syringe movement only — it does NOT change reservoir "
                "routing, so it draws from / pushes to wherever the valves "
                'currently point. Use "Set valves to reservoir" below first.'
            )
        )
        self.stroke_btn.clicked.connect(self._on_stroke)
        return stroke

    def _build_move_panel(self):
        # Pump move between reservoirs
        move = QGroupBox("Pump move (input → output reservoir)")
        mform = QFormLayout(move)
        self.move_pump = QComboBox()
        self.move_pump.addItems(["pump_a", "pump_out"])
        self.move_vol = QLineEdit("100")
        self.move_vel = QLineEdit()
        self.move_vel.setPlaceholderText("default")
        self.move_pickup_res = QLineEdit()
        self.move_pickup_res.setPlaceholderText("reservoir id (optional)")
        self.move_dispense_res = QLineEdit()
        self.move_dispense_res.setPlaceholderText("reservoir id (optional)")
        self.move_pickup_dir = QComboBox()
        self.move_pickup_dir.addItems(["in", "out"])
        self.move_dispense_dir = QComboBox()
        self.move_dispense_dir.addItems(["in", "out"])
        self.move_pickup_res.setToolTip(
            "Reservoir id to route to before each pickup. Leave blank to "
            "keep the current routing. Only reservoirs the loaded design "
            "uses can be routed to here."
        )
        self.move_dispense_res.setToolTip(
            "Reservoir id to route to before each dispense. Leave blank to "
            "keep the current routing."
        )
        self.move_vol.setToolTip(
            "Total volume, split into syringe-sized strokes; the valves are "
            "re-routed for every pickup and dispense of each stroke."
        )
        self.move_vel.setToolTip("Blank uses the design's max_velocity.")
        mform.addRow("Pump", self.move_pump)
        mform.addRow("Volume (µL)", self.move_vol)
        mform.addRow("Velocity (µL/min)", self.move_vel)
        mform.addRow("Pickup reservoir", self.move_pickup_res)
        mform.addRow("Dispense reservoir", self.move_dispense_res)
        mform.addRow("Pickup dir", self.move_pickup_dir)
        mform.addRow("Dispense dir", self.move_dispense_dir)
        self.move_btn = QPushButton("Run move")
        mform.addRow(self.move_btn)
        mform.addRow(
            self._hint(
                "Sets the valves itself: routes to the pickup reservoir, draws, "
                "routes to the dispense reservoir, pushes — repeating per "
                "syringe stroke. Anything already in the syringe is dispensed "
                "first. Reservoir routing here goes through the loaded design, "
                "so ids it does not use are rejected."
            )
        )
        self.move_btn.clicked.connect(self._on_move)
        return move

    def _build_full_move_panel(self):
        # Full fluid move: the protocol's inject done manually — pump_a drives
        # liquid from a reservoir to the sample while pump_out simultaneously
        # extracts from the sample to waste.
        full = QGroupBox("Full fluid move (inject + extract)")
        fform = QFormLayout(full)
        # Reservoir to inject from — the setup's manifold, like the valve
        # dropdown, so the plumbing can be exercised beyond the design's set.
        self.full_res = QComboBox()
        self.full_vol = QLineEdit("100")
        self.full_vel = QLineEdit()
        self.full_vel.setPlaceholderText("default")
        self.full_ef = QLineEdit()
        self.full_ef.setPlaceholderText("default")
        self.full_res.setToolTip(
            "Reservoir pump_a draws from, routed before the move. Every "
            "reservoir wired in the selected setup's fluid.reservoirs."
        )
        self.full_vol.setToolTip(
            "Total volume to inject into the sample; split into syringe "
            "strokes automatically. pump_out extracts alongside it."
        )
        self.full_vel.setToolTip(
            "Injection velocity. Blank uses the design's max_velocity."
        )
        self.full_ef.setToolTip(
            "Extraction-to-injection flow-rate ratio for pump_out. Blank "
            "uses the design's extractionfactor. Raise it above 1 to extract "
            "faster than injecting and avoid spillage."
        )
        fform.addRow("Reservoir", self.full_res)
        fform.addRow("Volume (µL)", self.full_vol)
        fform.addRow("Velocity (µL/min)", self.full_vel)
        fform.addRow("Extraction factor", self.full_ef)
        self.full_btn = QPushButton("Run full move")
        fform.addRow(self.full_btn)
        fform.addRow(
            self._hint(
                "Routes the valves to the reservoir, then runs pump_a "
                "(reservoir → sample) and pump_out (sample → waste) together "
                "— the same two-pump exchange a Run Sequence 'inject' step "
                "performs."
            )
        )
        self.full_btn.clicked.connect(self._on_full_move)
        return full

    def _build_valve_group(self):
        # Direct valve setting. The reservoirs offered come from the *setup's*
        # manifold, not the loaded design — testing the plumbing means
        # reaching every wired reservoir, including ones no design names.
        valve = QGroupBox("Set valves to reservoir")
        vform = QFormLayout(valve)
        self.valve_res = QComboBox()
        self.valve_res.setToolTip(
            "Every reservoir wired in the selected setup's fluid.reservoirs "
            "— including ones the loaded experiment design does not use."
        )
        self.valve_btn = QPushButton("Set valves")
        vform.addRow("Reservoir", self.valve_res)
        # What this reservoir's routing actually does, for the selected id.
        self.valve_route = self._hint("")
        vform.addRow(self.valve_route)
        vform.addRow(self.valve_btn)
        vform.addRow(
            self._hint(
                "Routing only — no liquid is moved. This is the route the pump "
                "controls above will then use."
            )
        )
        # Open / close every ibidi multiplexer channel at once (ibidi setups
        # only). Unlike a Hamilton rotary valve — always at exactly one port —
        # the multiplexer's 24 valves are independent, so they can all be
        # closed (no reservoir feeds the pump) or all opened at once.
        valve_all_row = QHBoxLayout()
        self.open_valves_btn = QPushButton("Open all valves")
        self.close_valves_btn = QPushButton("Close all valves")
        self.open_valves_btn.setToolTip(
            "Open every ibidi multiplexer channel at once. Only available on "
            "setups that use the ibidi multiplexer."
        )
        self.close_valves_btn.setToolTip(
            "Close every ibidi multiplexer channel, so no reservoir is "
            "connected to the pump. Only available on setups that use the "
            "ibidi multiplexer."
        )
        valve_all_row.addWidget(self.open_valves_btn)
        valve_all_row.addWidget(self.close_valves_btn)
        vform.addRow(valve_all_row)
        self.open_valves_btn.clicked.connect(self._on_open_valves)
        self.close_valves_btn.clicked.connect(self._on_close_valves)
        self.valve_btn.clicked.connect(self._on_set_valves)
        self.valve_res.currentIndexChanged.connect(self._update_route_hint)
        return valve

    def refresh(self):
        """Update the connection label and the setup's reservoir list."""
        connected = self._svc.fluid_system is not None
        self.status_label.setText(
            "connected" if connected else "not connected"
        )
        # The setup (hence its manifold) can change between refreshes.
        self._refresh_reservoirs()
        # The setup also fixes the schematic's wiring topology.
        self._reload_topology()
        # The connected design's cleaning config drives the volume estimate.
        self._refresh_clean_estimate()

    def _reload_topology(self):
        """Rebuild the schematic's static wiring from the loaded setup."""
        try:
            self.schematic.set_topology(self._svc.fluid_topology())
        except Exception:  # pragma: no cover - schematic is best-effort
            self.schematic.set_topology(None)
        self._refresh_schematic_state()

    def _refresh_schematic_state(self):
        """Push the latest cached valve/syringe snapshot to the schematic."""
        try:
            self.schematic.set_state(self._svc.fluid_state())
            # Reservoir names + which the design uses (cheap; no serial I/O).
            self.schematic.set_reservoir_labels(
                self._svc.fluid_reservoir_labels()
            )
            self.schematic.set_waste_labels(self._svc.fluid_waste_labels())
        except Exception:  # pragma: no cover - never let the timer die
            self.schematic.set_state(None)

    def set_replay_mode(self, on):
        """Suspend the live state poll so replayed snapshots are not overwritten.

        During post-run replay the schematic is driven by recorded fluid-state
        snapshots (:meth:`set_schematic_state`); the 300 ms live poll would
        otherwise clobber them with the (idle) current hardware state.
        """
        if on:
            self._poll_timer.stop()
        elif not self._poll_timer.isActive():
            self._poll_timer.start()
            self._refresh_schematic_state()

    def set_schematic_state(self, state):
        """Push a recorded valve/syringe snapshot to the schematic (replay)."""
        try:
            self.schematic.set_state(state)
        except Exception:  # pragma: no cover - schematic is best-effort
            pass

    def _on_channel_clicked(self, channel):
        """Toggle one ibidi channel open/closed from a port click."""
        if self._run_locked or self._svc.fluid_system is None:
            return
        self._run(
            lambda: self._svc.toggle_multiplexer_channel(channel),
            "Toggle channel {}".format(channel),
        )

    def _on_pump_clicked(self, pump_name):
        """Toggle a pump's syringe valve (in <-> out) from a pump click."""
        if self._run_locked or self._svc.fluid_system is None:
            return
        self._run(
            lambda: self._svc.toggle_pump_valve(pump_name),
            "Toggle {} valve".format(pump_name),
        )

    def set_status_text(self, text):
        """Set the status label (e.g. 'connecting…') from the coordinator."""
        self.status_label.setText(text)

    def set_run_lock(self, locked):
        """Disable manual fluid controls during a run; STOP stays available.

        The orchestrator owns the fluid system while running, so manual
        connect / tubing / pump actions must not be issued. The emergency
        STOP button is intentionally left enabled.
        """
        self._run_locked = locked  # also gates schematic click-toggles
        self.connect_btn.setEnabled(not locked)
        for btn in self._busy_buttons:
            btn.setEnabled(not locked)

    def _on_connect_clicked(self):
        if self._on_connect is not None:
            self._on_connect()

    # --- handlers -----------------------------------------------------

    def _on_fill(self):
        self._run(self._svc.fill_tubings, "Fill tubings")

    def _on_clean(self):
        # The design's cleaning section may waive the needles-connected
        # confirmation; respect it (default: ask).
        try:
            require_confirm = self._svc.cleaning_requires_confirmation()
        except Exception:  # pragma: no cover - default to asking
            require_confirm = True
        if require_confirm:
            reply = QMessageBox.question(
                self,
                "Clean tubings",
                "Start the tubing cleaning procedure?\n\n"
                "Make sure the input and output needles are in the same "
                "container (fluidly connected) and cleaning reservoirs are "
                "connected to their tanks.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._run(self._svc.clean_tubings, "Clean tubings")

    def _on_stroke(self):
        vol, ok = self._num(self.stroke_vol, "Volume", True, float)
        if not ok:
            return
        vel, ok = self._num(self.stroke_vel, "Velocity", False, float)
        if not ok:
            return
        kwargs = dict(
            vol=vol,
            action=self.stroke_action.currentData(),
            port=self.stroke_port.currentText(),
        )
        if vel is not None:
            kwargs["velocity"] = vel
        self._run(
            lambda: self._svc.single_stroke(
                self.stroke_pump.currentText(), **kwargs
            ),
            "Pump stroke",
        )

    def _on_move(self):
        vol, ok = self._num(self.move_vol, "Volume", True, float)
        if not ok:
            return
        vel, ok = self._num(self.move_vel, "Velocity", False, float)
        if not ok:
            return
        pres, ok = self._num(
            self.move_pickup_res, "Pickup reservoir", False, int
        )
        if not ok:
            return
        dres, ok = self._num(
            self.move_dispense_res, "Dispense reservoir", False, int
        )
        if not ok:
            return
        kwargs = dict(
            vol=vol,
            pickup_dir=self.move_pickup_dir.currentText(),
            dispense_dir=self.move_dispense_dir.currentText(),
        )
        if vel is not None:
            kwargs["velocity"] = vel
        if pres is not None:
            kwargs["pickup_res"] = pres
        if dres is not None:
            kwargs["dispense_res"] = dres
        self._run(
            lambda: self._svc.manual_pump(
                self.move_pump.currentText(), **kwargs
            ),
            "Pump move",
        )

    def _refresh_reservoirs(self):
        """Re-fill the reservoir dropdowns from the loaded setup's manifold."""
        ids = list(self._svc.reservoir_ids())
        # Both the valve-routing and full-move dropdowns offer the setup's
        # manifold; refill each while preserving its current selection.
        for combo in (self.valve_res, self.full_res):
            current = combo.currentData()
            combo.clear()
            for rid in ids:
                combo.addItem(str(rid), rid)
            if current is not None:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
        self.valve_btn.setEnabled(self.valve_res.count() > 0)
        self.full_btn.setEnabled(self.full_res.count() > 0)
        # "Open/Close all valves" are only meaningful for the ibidi multiplexer.
        has_mux = self._svc.has_multiplexer()
        self.open_valves_btn.setVisible(has_mux)
        self.close_valves_btn.setVisible(has_mux)
        self._update_route_hint()

    def _update_route_hint(self):
        """Describe the selected reservoir's route below the dropdown."""
        if getattr(self, "valve_route", None) is None:
            return  # called from _refresh_reservoirs during construction
        rid = self.valve_res.currentData()
        # Mirror the selection onto the schematic's highlighted path (the
        # widget may not exist yet during construction).
        schematic = getattr(self, "schematic", None)
        if schematic is not None:
            schematic.highlight_reservoir(rid)
        if rid is None:
            self.valve_route.setText(
                "No reservoirs wired — select a setup first."
            )
            return
        try:
            self.valve_route.setText(self._svc.describe_reservoir_route(rid))
        except Exception as exc:
            self.valve_route.setText("Route unavailable: {!r}".format(exc))

    def _on_set_valves(self):
        rid = self.valve_res.currentData()
        if rid is None:
            QMessageBox.warning(
                self,
                "No reservoir",
                "The selected setup wires no reservoirs to route to.",
            )
            return
        self._run(lambda: self._svc.set_valves(rid), "Set valves")

    def _on_full_move(self):
        rid = self.full_res.currentData()
        if rid is None:
            QMessageBox.warning(
                self,
                "No reservoir",
                "The selected setup wires no reservoirs to inject from.",
            )
            return
        vol, ok = self._num(self.full_vol, "Volume", True, float)
        if not ok:
            return
        vel, ok = self._num(self.full_vel, "Velocity", False, float)
        if not ok:
            return
        ef, ok = self._num(self.full_ef, "Extraction factor", False, float)
        if not ok:
            return
        kwargs = {}
        if vel is not None:
            kwargs["velocity"] = vel
        if ef is not None:
            kwargs["extractionfactor"] = ef
        self._run(
            lambda: self._svc.full_fluid_move(rid, vol, **kwargs),
            "Full fluid move",
        )

    def _on_open_valves(self):
        self._run(self._svc.open_all_valves, "Open all valves")

    def _on_close_valves(self):
        self._run(self._svc.close_all_valves, "Close all valves")

    def _on_stop(self):
        # Always safe; SystemService.stop_all_moves swallows errors.
        self._svc.stop_all_moves()

    # --- helpers ------------------------------------------------------

    def _num(self, edit, name, required, conv):
        """Parse a line edit; return (value_or_None, ok). Warns on bad input.

        Empty + not required -> (None, True). Empty + required, or a value
        that fails ``conv`` -> a warning + (None, False).
        """
        text = edit.text().strip()
        if text == "":
            if required:
                QMessageBox.warning(
                    self, "Invalid input", "{} is required.".format(name)
                )
                return None, False
            return None, True
        try:
            return conv(text), True
        except ValueError:
            QMessageBox.warning(
                self, "Invalid input", "{} must be a number.".format(name)
            )
            return None, False

    def _run(self, call, what):
        """Run a (potentially long) fluid op off the GUI thread.

        Disables the action buttons while it runs (the serial bus serves one
        op at a time); STOP stays enabled. Errors surface via a dialog.
        """
        if self._busy:
            return
        self._set_busy(True)
        run_in_background(
            self,
            call,
            on_done=lambda _: self._on_op_done(),
            on_error=lambda exc: self._on_op_error(exc, what),
        )

    def _on_op_done(self):
        self._set_busy(False)
        # Reflect the op's effect on the schematic at once (before the next
        # poll tick), so a toggle looks instantaneous.
        self._refresh_schematic_state()

    def _on_op_error(self, exc, what):
        self._set_busy(False)
        QMessageBox.critical(
            self, "{} failed".format(what), "{!r}".format(exc)
        )

    def _set_busy(self, busy):
        self._busy = busy
        for btn in self._busy_buttons:
            btn.setEnabled(not busy)
