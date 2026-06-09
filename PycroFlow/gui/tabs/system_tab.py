"""System tab: per-subsystem connection state + connect actions.

The first tab in the GUI. Shows whether the fluid, imaging and illumination
systems are connected and lets the user initiate each connection. The
connect logic lives in :class:`PycroFlow.services.system_service.SystemService`
(frontend-agnostic); on success the connected systems are mirrored into the
:class:`PycroFlow.services.experiment_service.ExperimentService` so a
subsequent run builds the orchestrator with them.
"""
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton, QGroupBox,
    QFileDialog, QMessageBox,
)


_CONNECTED_STYLE = "color: green; font-weight: bold;"
_DISCONNECTED_STYLE = "color: #b00000; font-weight: bold;"


class SystemTab(QWidget):
    def __init__(self, system_service, experiment_service=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._experiment_service = experiment_service
        self._status_labels = {}
        # Callbacks fired after a successful connect, so sibling tabs
        # (Fluid, Imaging) can refresh their own status displays.
        self._connection_listeners = []
        self._build_ui()
        self.refresh()

    def add_connection_listener(self, fn):
        """Register a no-arg callback fired after a successful connect."""
        self._connection_listeners.append(fn)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        box = QGroupBox("System connections")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("<b>System</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Status</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Action</b>"), 0, 2)

        rows = [
            ("fluid", "Fluid (Hamilton)", self._on_connect_fluid),
            ("imaging", "Imaging (Micro-Manager)", self._on_connect_imaging),
            ("illumination", "Illumination (monet)",
             self._on_connect_illumination),
        ]
        for r, (key, label, handler) in enumerate(rows, start=1):
            grid.addWidget(QLabel(label), r, 0)
            status = QLabel()
            self._status_labels[key] = status
            grid.addWidget(status, r, 1)
            btn = QPushButton("Connect…")
            btn.clicked.connect(handler)
            grid.addWidget(btn, r, 2)

        layout.addWidget(box)
        layout.addStretch()

    def refresh(self):
        """Update the status labels from the service's connection states."""
        states = self._svc.connection_states()
        for key, label in self._status_labels.items():
            connected = states.get(key, False)
            label.setText("Connected" if connected else "Not connected")
            label.setStyleSheet(
                _CONNECTED_STYLE if connected else _DISCONNECTED_STYLE)

    # --- connect handlers ---------------------------------------------

    def _on_connect_fluid(self):
        hamilton = self._resolve_config(
            "hamilton_config.yaml", "Select Hamilton system config")
        if hamilton is None:
            return
        tubing = self._resolve_config(
            "tubing_config.yaml", "Select Hamilton tubing config")
        if tubing is None:
            return
        self._do_connect(
            lambda: self._svc.connect_fluid(hamilton, tubing), "fluid")

    def _on_connect_imaging(self):
        imaging = self._resolve_config(
            "imaging_config.yaml", "Select imaging config")
        if imaging is None:
            return
        self._do_connect(
            lambda: self._svc.connect_imaging(imaging), "imaging")

    def _on_connect_illumination(self):
        # IlluminationSystem needs no config at construction; the monet
        # control loads when a protocol with an illumination 'setup' runs.
        self._do_connect(self._svc.connect_illumination, "illumination")

    # --- helpers ------------------------------------------------------

    def _resolve_config(self, default_name, dialog_title):
        """Ask for a config file, pre-selecting the cwd default if present.

        Returns the chosen path, or None if the user cancels.
        """
        start = default_name if os.path.exists(default_name) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, dialog_title, start, "YAML files (*.yaml *.yml)")
        return path or None

    def _do_connect(self, connect_call, key):
        try:
            connect_call()
        except Exception as exc:
            QMessageBox.critical(
                self, "Connection failed",
                "Could not connect the {} system:\n\n{!r}".format(key, exc))
            self.refresh()
            return
        self._mirror_to_experiment_service()
        self.refresh()
        for fn in self._connection_listeners:
            try:
                fn()
            except Exception:
                pass

    def _mirror_to_experiment_service(self):
        """Push the connected systems into the ExperimentService.

        Best-effort: if an experiment is already active the attach is
        refused, but the connection still stands on the SystemService for
        manual control.
        """
        if self._experiment_service is None:
            return
        try:
            self._experiment_service.attach_systems(
                fluid_system=self._svc.fluid_system,
                imaging_system=self._svc.imaging_system,
                illumination_system=self._svc.illumination_system,
            )
        except Exception:
            pass
