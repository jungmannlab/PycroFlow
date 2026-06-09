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
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QComboBox, QMessageBox,
)

from PycroFlow.gui.widgets.worker import run_in_background


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
        self._build_ui()
        # Buttons disabled while a fluid op runs in the background (the serial
        # bus serves one operation at a time). STOP stays enabled.
        self._busy_buttons = [
            self.fill_btn, self.clean_btn, self.stroke_btn,
            self.move_btn, self.valve_btn,
        ]

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- status + connect
        status_box = QGroupBox("Fluid system")
        status_row = QHBoxLayout(status_box)
        connected = self._svc.fluid_system is not None
        self.status_label = QLabel(
            "connected" if connected else "not connected")
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

    def _build_tubing_group(self):
        box = QGroupBox("Tubing operations")
        row = QHBoxLayout(box)
        self.fill_btn = QPushButton("Fill tubings")
        self.clean_btn = QPushButton("Clean tubings")
        row.addWidget(self.fill_btn)
        row.addWidget(self.clean_btn)
        row.addStretch()
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
        sform.addRow("Pump", self.stroke_pump)
        sform.addRow("Volume (µL)", self.stroke_vol)
        sform.addRow("Velocity (µL/min)", self.stroke_vel)
        sform.addRow("Pickup", self.stroke_pickup)
        sform.addRow("Dispense", self.stroke_dispense)
        self.stroke_btn = QPushButton("Run stroke")
        sform.addRow(self.stroke_btn)
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
        mform.addRow("Pump", self.move_pump)
        mform.addRow("Volume (µL)", self.move_vol)
        mform.addRow("Velocity (µL/min)", self.move_vel)
        mform.addRow("Pickup reservoir", self.move_pickup_res)
        mform.addRow("Dispense reservoir", self.move_dispense_res)
        mform.addRow("Pickup dir", self.move_pickup_dir)
        mform.addRow("Dispense dir", self.move_dispense_dir)
        self.move_btn = QPushButton("Run move")
        mform.addRow(self.move_btn)
        self.move_btn.clicked.connect(self._on_move)
        outer.addWidget(move)

        # Direct valve setting
        valve = QGroupBox("Set valves to reservoir")
        vform = QFormLayout(valve)
        self.valve_res = QLineEdit()
        self.valve_res.setPlaceholderText("reservoir id")
        self.valve_btn = QPushButton("Set valves")
        vform.addRow("Reservoir", self.valve_res)
        vform.addRow(self.valve_btn)
        self.valve_btn.clicked.connect(self._on_set_valves)
        outer.addWidget(valve)

        return box

    def refresh(self):
        """Update the connection label from the fluid system."""
        connected = self._svc.fluid_system is not None
        self.status_label.setText(
            "connected" if connected else "not connected")

    def set_status_text(self, text):
        """Set the status label (e.g. 'connecting…') from the coordinator."""
        self.status_label.setText(text)

    def _on_connect_clicked(self):
        if self._on_connect is not None:
            self._on_connect()

    # --- handlers -----------------------------------------------------

    def _on_fill(self):
        self._run(self._svc.fill_tubings, "Fill tubings")

    def _on_clean(self):
        reply = QMessageBox.question(
            self, "Clean tubings",
            "Start the tubing cleaning procedure?\n\n"
            "Make sure the input and output needles are in the same "
            "container (fluidly connected) and cleaning reservoirs are "
            "connected to their tanks.")
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
            kwargs['velocity'] = vel
        self._run(
            lambda: self._svc.manual_pump(
                self.stroke_pump.currentText(), **kwargs),
            "Pump stroke")

    def _on_move(self):
        vol, ok = self._num(self.move_vol, "Volume", True, float)
        if not ok:
            return
        vel, ok = self._num(self.move_vel, "Velocity", False, float)
        if not ok:
            return
        pres, ok = self._num(
            self.move_pickup_res, "Pickup reservoir", False, int)
        if not ok:
            return
        dres, ok = self._num(
            self.move_dispense_res, "Dispense reservoir", False, int)
        if not ok:
            return
        kwargs = dict(
            vol=vol,
            pickup_dir=self.move_pickup_dir.currentText(),
            dispense_dir=self.move_dispense_dir.currentText(),
        )
        if vel is not None:
            kwargs['velocity'] = vel
        if pres is not None:
            kwargs['pickup_res'] = pres
        if dres is not None:
            kwargs['dispense_res'] = dres
        self._run(
            lambda: self._svc.manual_pump(
                self.move_pump.currentText(), **kwargs),
            "Pump move")

    def _on_set_valves(self):
        rid, ok = self._num(self.valve_res, "Reservoir id", True, int)
        if not ok:
            return
        self._run(lambda: self._svc.set_valves(rid), "Set valves")

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
        if text == '':
            if required:
                QMessageBox.warning(
                    self, "Invalid input", "{} is required.".format(name))
                return None, False
            return None, True
        try:
            return conv(text), True
        except ValueError:
            QMessageBox.warning(
                self, "Invalid input",
                "{} must be a number.".format(name))
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
            self, call,
            on_done=lambda _: self._set_busy(False),
            on_error=lambda exc: self._on_op_error(exc, what))

    def _on_op_error(self, exc, what):
        self._set_busy(False)
        QMessageBox.critical(
            self, "{} failed".format(what), "{!r}".format(exc))

    def _set_busy(self, busy):
        self._busy = busy
        for btn in self._busy_buttons:
            btn.setEnabled(not busy)
