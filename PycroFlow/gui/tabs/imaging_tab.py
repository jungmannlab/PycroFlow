"""Imaging tab: parameters, PFS status, last-acquisition summary.

Read-only view for now — live preview and a graphical acquisition editor are
explicitly out of scope for the initial GUI (see the plan's Stage 5 notes).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGroupBox, QFormLayout,
)


class ImagingTab(QWidget):
    def __init__(self, system_service, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        status_box = QGroupBox("Imaging system")
        form = QFormLayout(status_box)
        connected = self._svc.imaging_system is not None
        self.status_label = QLabel("connected" if connected else "not connected")
        form.addRow("Status", self.status_label)
        self.pfs_label = QLabel("—")
        form.addRow("PFS", self.pfs_label)
        self.last_acq_label = QLabel("—")
        form.addRow("Last acquisition", self.last_acq_label)
        layout.addWidget(status_box)
        layout.addStretch()

    def refresh(self):
        """Pull current status from the imaging system (called by a timer or
        on tab activation). No-op when no imaging system is configured."""
        imaging = self._svc.imaging_system
        if imaging is None:
            return
        # Best-effort, defensive: hardware attributes may be absent under
        # mocks or before the first acquisition.
        pfs = getattr(imaging, 'last_pfs_status', None)
        if pfs is not None:
            self.pfs_label.setText(str(pfs))
