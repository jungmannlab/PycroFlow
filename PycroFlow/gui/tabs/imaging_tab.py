"""Imaging tab: parameters, PFS status, last-acquisition summary.

Read-only view for now — live preview and a graphical acquisition editor are
explicitly out of scope for the initial GUI (see the plan's Stage 5 notes).
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QFormLayout,
    QPushButton,
)


class ImagingTab(QWidget):
    def __init__(self, system_service, on_connect=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._on_connect = on_connect
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        status_box = QGroupBox("Imaging system")
        box_layout = QVBoxLayout(status_box)
        status_row = QHBoxLayout()
        connected = self._svc.imaging_system is not None
        self.status_label = QLabel(
            "connected" if connected else "not connected"
        )
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        status_row.addWidget(self.connect_btn)
        box_layout.addLayout(status_row)
        form = QFormLayout()
        self.pfs_label = QLabel("—")
        form.addRow("PFS", self.pfs_label)
        self.last_acq_label = QLabel("—")
        form.addRow("Last acquisition", self.last_acq_label)
        box_layout.addLayout(form)
        layout.addWidget(status_box)
        layout.addStretch()

    def set_status_text(self, text):
        """Set the status label (e.g. 'connecting…') from the coordinator."""
        self.status_label.setText(text)

    def set_run_lock(self, locked):
        """Disable the connect control while an experiment is running."""
        self.connect_btn.setEnabled(not locked)

    def _on_connect_clicked(self):
        if self._on_connect is not None:
            self._on_connect()

    def refresh(self):
        """Pull current status from the imaging system.

        Called when the System tab connects/disconnects hardware, on a timer,
        or on tab activation. Updates the connection label always; PFS only
        when an imaging system is present.
        """
        imaging = self._svc.imaging_system
        self.status_label.setText(
            "connected" if imaging is not None else "not connected"
        )
        if imaging is None:
            return
        # Best-effort, defensive: hardware attributes may be absent under
        # mocks or before the first acquisition.
        pfs = getattr(imaging, "last_pfs_status", None)
        if pfs is not None:
            self.pfs_label.setText(str(pfs))
