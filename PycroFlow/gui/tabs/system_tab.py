"""System tab: choose a microscope setup + connect the subsystems.

The first tab in the GUI. Pick a per-microscope **setup** (which also drives
the Monet tab), then connect the fluid / imaging / illumination systems. The
connect logic lives in :class:`PycroFlow.services.SystemService`
(frontend-agnostic); on success the connected systems are mirrored into the
:class:`PycroFlow.services.ExperimentService`. Choosing the ``Emulator`` setup
connects emulated hardware so the whole app works without instruments.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QMessageBox,
)

from PycroFlow import configs
from PycroFlow.gui.widgets.worker import run_in_background


_CONNECTED_STYLE = "color: green; font-weight: bold;"
_DISCONNECTED_STYLE = "color: #b00000; font-weight: bold;"


class SystemTab(QWidget):
    def __init__(self, system_service, experiment_service=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._experiment_service = experiment_service
        self._status_labels = {}
        self._connect_buttons = {}
        self._busy = False
        # Fired after a successful connect (sibling tabs refresh status).
        self._connection_listeners = []
        # Fired after a setup is loaded, with the setup name (Monet tab).
        self._setup_listeners = []
        self._build_ui()
        self.refresh()

    def add_connection_listener(self, fn):
        """Register a no-arg callback fired after a successful connect."""
        self._connection_listeners.append(fn)

    def add_setup_listener(self, fn):
        """Register a callback fired with the setup name when one is loaded."""
        self._setup_listeners.append(fn)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        setup_box = QGroupBox("Microscope setup")
        srow = QHBoxLayout(setup_box)
        srow.addWidget(QLabel("Setup:"))
        self.setup_combo = QComboBox()
        self.setup_combo.addItems(configs.list_setups())
        srow.addWidget(self.setup_combo)
        self.load_setup_btn = QPushButton("Load setup")
        srow.addWidget(self.load_setup_btn)
        self.setup_status = QLabel("no setup loaded")
        srow.addWidget(self.setup_status)
        srow.addStretch()
        layout.addWidget(setup_box)
        self.load_setup_btn.clicked.connect(self._on_load_setup)

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
            self._connect_buttons[key] = btn
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

    # --- setup --------------------------------------------------------

    def _on_load_setup(self):
        name = self.setup_combo.currentText()
        if not name:
            return
        try:
            self._svc.load_setup(name)
        except Exception as exc:
            QMessageBox.critical(
                self, "Setup load failed", "{}".format(exc))
            return
        suffix = " (emulated)" if self._svc.is_emulated() else ""
        self.setup_status.setText("loaded: {}{}".format(name, suffix))
        for fn in self._setup_listeners:
            try:
                fn(self._svc.get_monet_setup())
            except Exception:
                pass

    # --- connect handlers ---------------------------------------------

    def _fluid_section(self):
        design = getattr(self._experiment_service, 'experiment_design', None)
        if design:
            return design.get('fluid')
        return None

    def _on_connect_fluid(self):
        if self._svc.setup is None:
            self._need_setup()
            return
        fluid = self._fluid_section()
        if not fluid or not fluid.get('settings'):
            QMessageBox.warning(
                self, "No experiment design",
                "Load or translate an experiment design first — the fluid "
                "system needs its reservoir list.")
            return
        self._do_connect(lambda: self._svc.connect_fluid(fluid), "fluid")

    def _on_connect_imaging(self):
        if self._svc.setup is None:
            self._need_setup()
            return
        # Imaging connect runs on the GUI thread: it touches the
        # Micro-Manager Core (pycromanager / ZMQ), best kept off worker
        # threads. It's a one-time, comparatively short step.
        if self._svc.is_emulated():
            self._do_connect(
                self._svc.connect_imaging, "imaging", background=False)
            return
        design = getattr(
            self._experiment_service, 'experiment_design', None) or {}
        cfg = configs.assemble_imaging_config(self._svc.setup, design)
        self._do_connect(
            lambda: self._svc.connect_imaging(cfg), "imaging",
            background=False)

    def _on_connect_illumination(self):
        if self._svc.setup is None:
            self._need_setup()
            return
        self._do_connect(self._svc.connect_illumination, "illumination")

    # --- helpers ------------------------------------------------------

    def _need_setup(self):
        QMessageBox.warning(
            self, "No setup", "Load a microscope setup first.")

    def _do_connect(self, connect_call, key, background=True):
        # Connecting can take many seconds (serial handshake) — run it off the
        # GUI thread so the UI stays responsive. ``background=False`` keeps it
        # on the GUI thread (used for imaging / Micro-Manager).
        if self._busy:
            return
        self._set_busy(True)
        label = self._status_labels[key]
        label.setText("Connecting…")
        label.setStyleSheet("")
        if not background:
            try:
                connect_call()
            except Exception as exc:
                self._connect_error(exc, key)
            else:
                self._connect_done()
            return
        run_in_background(
            self, connect_call,
            on_done=lambda _: self._connect_done(),
            on_error=lambda exc: self._connect_error(exc, key))

    def _connect_done(self):
        self._set_busy(False)
        self._mirror_to_experiment_service()
        self.refresh()
        for fn in self._connection_listeners:
            try:
                fn()
            except Exception:
                pass

    def _connect_error(self, exc, key):
        self._set_busy(False)
        self.refresh()
        QMessageBox.critical(
            self, "Connection failed",
            "Could not connect the {} system:\n\n{!r}".format(key, exc))

    def _set_busy(self, busy):
        self._busy = busy
        for btn in self._connect_buttons.values():
            btn.setEnabled(not busy)
        self.load_setup_btn.setEnabled(not busy)

    def _mirror_to_experiment_service(self):
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
