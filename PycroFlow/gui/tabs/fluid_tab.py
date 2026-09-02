"""Fluid tab: tubing ops, manual control, and an emergency stop.

Sections:
* **Tubing operations** — fill / clean tubings.
* **Manual control** — a single pump stroke, a pump move between an input and
  an output reservoir, and direct valve routing.
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
            self.fill_btn, self.clean_btn, self.stroke_btn,
            self.move_btn, self.valve_btn, self.close_valves_btn,
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
        sbl.addWidget(self._hint(
            "Click a port to toggle that ibidi channel open/closed; click a "
            "pump to flip its valve (in ↔ out). Raw overrides — routing is "
            "ignored."))
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
            "no air or old liquid is left between reservoir and sample.")
        self.clean_btn.setToolTip(
            "Run the cleaning procedure through the design's cleaning "
            "reservoirs. Needles must sit in the same container first.")
        row.addWidget(self.fill_btn)
        row.addWidget(self.clean_btn)
        row.addStretch()
        outer.addLayout(row)
        outer.addWidget(self._hint(
            "Whole-system procedures over the reservoirs the loaded "
            "experiment design uses — not the full setup manifold."))
        self.fill_btn.clicked.connect(self._on_fill)
        self.clean_btn.clicked.connect(self._on_clean)
        return box

    def _build_manual_group(self):
        box = QGroupBox("Manual control")
        outer = QVBoxLayout(box)

        # Single pump stroke
        stroke = QGroupBox("Single pump stroke")
        sform = QFormLayout(stroke)
        self.stroke_pump = QComboBox()
        self.stroke_pump.addItems(["pump_a", "pump_out"])
        self.stroke_vol = QLineEdit("100")
        self.stroke_vel = QLineEdit()
        self.stroke_vel.setPlaceholderText("default")
        self.stroke_pickup = QComboBox()
        self.stroke_pickup.addItems(["in", "out"])
        self.stroke_dispense = QComboBox()
        self.stroke_dispense.addItems(["out", "in"])
        self.stroke_pump.setToolTip(
            "pump_a drives liquid from a reservoir to the sample; "
            "pump_out is the extraction (waste) pump.")
        self.stroke_vol.setToolTip(
            "Total volume. Larger than the syringe is split into "
            "back-to-back syringe strokes automatically.")
        self.stroke_vel.setToolTip(
            "Blank uses the design's max_velocity. The pump rejects values "
            "outside its own range — check errors.log if nothing moves.")
        self.stroke_pickup.setToolTip(
            "Which port the syringe draws from: 'in' = the reservoir side "
            "(as currently routed), 'out' = the sample/waste side.")
        self.stroke_dispense.setToolTip(
            "Which port the syringe pushes to. Any liquid already in the "
            "syringe is dispensed here first.")
        sform.addRow("Pump", self.stroke_pump)
        sform.addRow("Volume (µL)", self.stroke_vol)
        sform.addRow("Velocity (µL/min)", self.stroke_vel)
        sform.addRow("Pickup", self.stroke_pickup)
        sform.addRow("Dispense", self.stroke_dispense)
        self.stroke_btn = QPushButton("Run stroke")
        sform.addRow(self.stroke_btn)
        sform.addRow(self._hint(
            "Moves the syringe only — it does NOT change reservoir routing, "
            "so it draws from wherever the valves currently point. Use "
            "\"Set valves to reservoir\" below first."))
        self.stroke_btn.clicked.connect(self._on_stroke)
        outer.addWidget(stroke)

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
            "uses can be routed to here.")
        self.move_dispense_res.setToolTip(
            "Reservoir id to route to before each dispense. Leave blank to "
            "keep the current routing.")
        self.move_vol.setToolTip(
            "Total volume, split into syringe-sized strokes; the valves are "
            "re-routed for every pickup and dispense of each stroke.")
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
        mform.addRow(self._hint(
            "Sets the valves itself: routes to the pickup reservoir, draws, "
            "routes to the dispense reservoir, pushes — repeating per "
            "syringe stroke. Anything already in the syringe is dispensed "
            "first. Reservoir routing here goes through the loaded design, "
            "so ids it does not use are rejected."))
        self.move_btn.clicked.connect(self._on_move)
        outer.addWidget(move)

        # Direct valve setting. The reservoirs offered come from the *setup's*
        # manifold, not the loaded design — testing the plumbing means
        # reaching every wired reservoir, including ones no design names.
        valve = QGroupBox("Set valves to reservoir")
        vform = QFormLayout(valve)
        self.valve_res = QComboBox()
        self.valve_res.setToolTip(
            "Every reservoir wired in the selected setup's fluid.reservoirs "
            "— including ones the loaded experiment design does not use.")
        self.valve_btn = QPushButton("Set valves")
        vform.addRow("Reservoir", self.valve_res)
        # What this reservoir's routing actually does, for the selected id.
        self.valve_route = self._hint("")
        vform.addRow(self.valve_route)
        vform.addRow(self.valve_btn)
        vform.addRow(self._hint(
            "Routing only — no liquid is moved. This is the route the pump "
            "controls above will then use."))
        # Close every ibidi multiplexer channel (ibidi setups only). Unlike a
        # Hamilton rotary valve, the multiplexer's 24 valves are independent
        # and can all be closed, connecting no reservoir to the pump.
        self.close_valves_btn = QPushButton("Close all valves")
        self.close_valves_btn.setToolTip(
            "Close every ibidi multiplexer channel, so no reservoir is "
            "connected to the pump. Only available on setups that use the "
            "ibidi multiplexer.")
        vform.addRow(self.close_valves_btn)
        self.close_valves_btn.clicked.connect(self._on_close_valves)
        self.valve_btn.clicked.connect(self._on_set_valves)
        self.valve_res.currentIndexChanged.connect(self._update_route_hint)
        outer.addWidget(valve)
        self._refresh_reservoirs()

        return box

    def refresh(self):
        """Update the connection label and the setup's reservoir list."""
        connected = self._svc.fluid_system is not None
        self.status_label.setText(
            "connected" if connected else "not connected")
        # The setup (hence its manifold) can change between refreshes.
        self._refresh_reservoirs()
        # The setup also fixes the schematic's wiring topology.
        self._reload_topology()

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
        except Exception:  # pragma: no cover - never let the timer die
            self.schematic.set_state(None)

    def _on_channel_clicked(self, channel):
        """Toggle one ibidi channel open/closed from a port click."""
        if self._run_locked or self._svc.fluid_system is None:
            return
        self._run(
            lambda: self._svc.toggle_multiplexer_channel(channel),
            "Toggle channel {}".format(channel))

    def _on_pump_clicked(self, pump_name):
        """Toggle a pump's syringe valve (in <-> out) from a pump click."""
        if self._run_locked or self._svc.fluid_system is None:
            return
        self._run(
            lambda: self._svc.toggle_pump_valve(pump_name),
            "Toggle {} valve".format(pump_name))

    def set_status_text(self, text):
        """Set the status label (e.g. 'connecting…') from the coordinator."""
        self.status_label.setText(text)

    def set_run_lock(self, locked):
        """Disable manual fluid controls during a run; STOP stays available.

        The orchestrator owns the fluid system while running, so manual
        connect / tubing / pump actions must not be issued. The emergency
        STOP button is intentionally left enabled.
        """
        self._run_locked = locked   # also gates schematic click-toggles
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
        reply = QMessageBox.question(
            self,
            "Clean tubings",
            "Start the tubing cleaning procedure?\n\n"
            "Make sure the input and output needles are in the same "
            "container (fluidly connected) and cleaning reservoirs are "
            "connected to their tanks.",
        )
        if reply == QMessageBox.StandardButton.Yes:
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
            pickup_dir=self.stroke_pickup.currentText(),
            dispense_dir=self.stroke_dispense.currentText(),
        )
        if vel is not None:
            kwargs["velocity"] = vel
        self._run(
            lambda: self._svc.manual_pump(
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
        """Re-fill the reservoir dropdown from the loaded setup's manifold."""
        current = self.valve_res.currentData()
        self.valve_res.clear()
        for rid in self._svc.reservoir_ids():
            self.valve_res.addItem(str(rid), rid)
        if current is not None:
            index = self.valve_res.findData(current)
            if index >= 0:
                self.valve_res.setCurrentIndex(index)
        self.valve_btn.setEnabled(self.valve_res.count() > 0)
        # "Close all valves" is only meaningful for the ibidi multiplexer.
        self.close_valves_btn.setVisible(self._svc.has_multiplexer())
        self._update_route_hint()

    def _update_route_hint(self):
        """Describe the selected reservoir's route below the dropdown."""
        if getattr(self, 'valve_route', None) is None:
            return   # called from _refresh_reservoirs during construction
        rid = self.valve_res.currentData()
        # Mirror the selection onto the schematic's highlighted path (the
        # widget may not exist yet during construction).
        schematic = getattr(self, 'schematic', None)
        if schematic is not None:
            schematic.highlight_reservoir(rid)
        if rid is None:
            self.valve_route.setText(
                "No reservoirs wired — select a setup first.")
            return
        try:
            self.valve_route.setText(self._svc.describe_reservoir_route(rid))
        except Exception as exc:
            self.valve_route.setText("Route unavailable: {!r}".format(exc))

    def _on_set_valves(self):
        rid = self.valve_res.currentData()
        if rid is None:
            QMessageBox.warning(
                self, "No reservoir",
                "The selected setup wires no reservoirs to route to.")
            return
        self._run(lambda: self._svc.set_valves(rid), "Set valves")

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
