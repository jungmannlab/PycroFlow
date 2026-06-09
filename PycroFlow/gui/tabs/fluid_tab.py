"""Fluid tab: subsystem status + manual pump control.

Manual commands go through
:class:`PycroFlow.services.system_service.SystemService` so the tab never
reaches into private attributes of the fluid system.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout,
)


class FluidTab(QWidget):
    def __init__(self, system_service, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        status_box = QGroupBox("Fluid system")
        status_layout = QVBoxLayout(status_box)
        connected = self._svc.fluid_system is not None
        self.status_label = QLabel(
            "connected" if connected else "not connected")
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_box)

        # --- manual pump
        pump_box = QGroupBox("Manual pump")
        form = QFormLayout(pump_box)
        self.pump_name = QLineEdit("pump_a")
        self.pump_vol = QLineEdit("100")
        form.addRow("Pump", self.pump_name)
        form.addRow("Volume (µL)", self.pump_vol)
        btn_row = QHBoxLayout()
        self.fill_btn = QPushButton("Fill tubings")
        self.clean_btn = QPushButton("Clean tubings")
        self.stop_btn = QPushButton("Stop all moves")
        for b in (self.fill_btn, self.clean_btn, self.stop_btn):
            btn_row.addWidget(b)
        form.addRow(btn_row)
        layout.addWidget(pump_box)
        layout.addStretch()

        self.fill_btn.clicked.connect(self._on_fill)
        self.clean_btn.clicked.connect(self._on_clean)
        self.stop_btn.clicked.connect(self._on_stop)

    def refresh(self):
        """Update the connection label from the fluid system.

        Called when the System tab connects/disconnects hardware.
        """
        connected = self._svc.fluid_system is not None
        self.status_label.setText(
            "connected" if connected else "not connected")

    def _on_fill(self):
        self._svc.fill_tubings()

    def _on_clean(self):
        self._svc.clean_tubings()

    def _on_stop(self):
        # Always safe; SystemService.stop_all_moves swallows errors.
        self._svc.stop_all_moves()
