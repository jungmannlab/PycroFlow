"""PycroFlowMainWindow — the top-level GUI window.

A toolbar (setup selector + run controls) over a tab widget: Experiment
Design, Run Sequence, Fluid, Imaging, Monet. The window also coordinates
hardware connection: the microscope **setup** is chosen in the toolbar, and
subsystems **autoconnect** once an experiment design is loaded (the fluid
system needs the design's reservoir list). Each subsystem tab shows its
connection status and offers a manual Connect/Reconnect.

Owns the :class:`QtBridge` that marshals ExperimentService observer callbacks
onto the GUI thread.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QToolBar, QFileDialog, QLabel, QComboBox,
    QMessageBox,
)
# Qt6 moved QAction out of QtWidgets into QtGui.
from PyQt6.QtGui import QAction

from PycroFlow import configs
from PycroFlow.gui.qt_bridge import QtBridge
from PycroFlow.gui.widgets.worker import run_in_background
from PycroFlow.gui.tabs.experiment_design_tab import ExperimentDesignTab
from PycroFlow.gui.tabs.experiment_tab import ExperimentTab
from PycroFlow.gui.tabs.fluid_tab import FluidTab
from PycroFlow.gui.tabs.imaging_tab import ImagingTab
from PycroFlow.gui.tabs.monet_tab import MonetTab


class PycroFlowMainWindow(QMainWindow):
    def __init__(self, experiment_service, system_service, parent=None):
        super().__init__(parent)
        self._experiment_service = experiment_service
        self._system_service = system_service
        self._bridge = QtBridge(experiment_service, parent=self)
        # Keys of subsystems whose connect is currently in flight.
        self._connecting = set()

        self.setWindowTitle("PycroFlow")
        self._build_toolbar()
        self._build_tabs()
        self._init_setup()

    # --- toolbar ------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        self.addToolBar(tb)

        tb.addWidget(QLabel("Setup: "))
        self.setup_combo = QComboBox()
        self.setup_combo.addItems(configs.list_setups())
        tb.addWidget(self.setup_combo)
        self.act_connect = QAction("Connect", self)
        tb.addAction(self.act_connect)
        tb.addSeparator()

        self.act_load = QAction("Load run sequence", self)
        self.act_start = QAction("Start", self)
        self.act_pause = QAction("Pause", self)
        self.act_abort = QAction("Abort", self)
        for a in (self.act_load, self.act_start, self.act_pause,
                  self.act_abort):
            tb.addAction(a)

        self.setup_combo.currentTextChanged.connect(self._on_setup_changed)
        self.act_connect.triggered.connect(self._autoconnect)
        self.act_load.triggered.connect(self._on_load)
        self.act_start.triggered.connect(
            lambda: self._experiment_service.start())
        self.act_pause.triggered.connect(
            lambda: self._experiment_service.pause())
        self.act_abort.triggered.connect(
            lambda: self._experiment_service.abort())

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.design_tab = ExperimentDesignTab(
            self._experiment_service,
            on_translated=self._on_translated,
            on_design_loaded=self._on_design_changed)
        self.run_sequence_tab = ExperimentTab(
            self._experiment_service, self._bridge)
        self.fluid_tab = FluidTab(
            self._system_service,
            on_connect=lambda: self._connect_system('fluid'))
        self.imaging_tab = ImagingTab(
            self._system_service,
            on_connect=lambda: self._connect_system('imaging'))
        self.monet_tab = MonetTab()

        self.tabs.addTab(self.design_tab, "Experiment Design")
        self.tabs.addTab(self.run_sequence_tab, "Run Sequence")
        self.tabs.addTab(self.fluid_tab, "Fluid")
        self.tabs.addTab(self.imaging_tab, "Imaging")
        self.tabs.addTab(self.monet_tab, "Monet")
        self.setCentralWidget(self.tabs)

    # --- setup / connection -------------------------------------------

    def _init_setup(self):
        if self.setup_combo.count():
            # Selecting index 0 fires currentTextChanged -> _on_setup_changed
            # unless it's already current; load explicitly to be sure.
            self._on_setup_changed(self.setup_combo.currentText())

    def _on_setup_changed(self, name):
        if not name:
            return
        try:
            self._system_service.load_setup(name)
        except Exception as exc:
            QMessageBox.critical(self, "Setup load failed", "{}".format(exc))
            return
        self.monet_tab.set_setup(self._system_service.get_monet_setup())
        self._refresh_status()
        # If a design is already loaded, (re)connect for the new setup.
        if self._experiment_service.experiment_design:
            self._autoconnect()

    def _on_design_changed(self):
        self._autoconnect()

    def _autoconnect(self):
        if self._system_service.setup is None:
            return
        for key in ('illumination', 'imaging', 'fluid'):
            if not self._is_connected(key):
                self._connect_system(key, warn_missing=False)

    def _connect_system(self, key, warn_missing=True):
        if self._system_service.setup is None:
            if warn_missing:
                QMessageBox.warning(
                    self, "No setup", "Select a microscope setup first.")
            return
        if key in self._connecting:
            return
        call = self._connect_call(key, warn_missing)
        if call is None:
            return
        self._connecting.add(key)
        self._set_tab_connecting(key)

        def done(_):
            self._connecting.discard(key)
            self._mirror_systems()
            self._refresh_status()

        def err(exc):
            self._connecting.discard(key)
            self._refresh_status()
            QMessageBox.critical(
                self, "Connection failed",
                "Could not connect the {} system:\n\n{!r}".format(key, exc))

        # Imaging touches the Micro-Manager Core (pycromanager/ZMQ): keep it
        # on the GUI thread. Fluid (serial) and illumination run in the
        # background so the UI stays responsive.
        if key == 'imaging':
            try:
                call()
            except Exception as exc:
                err(exc)
            else:
                done(None)
        else:
            run_in_background(self, call, on_done=done, on_error=err)

    def _connect_call(self, key, warn_missing):
        svc = self._system_service
        if key == 'fluid':
            design = self._experiment_service.experiment_design
            fluid = (design or {}).get('fluid')
            if not fluid or not fluid.get('settings'):
                if warn_missing:
                    QMessageBox.warning(
                        self, "No experiment design",
                        "Load an experiment design first — the fluid system "
                        "needs its reservoir list.")
                return None
            return lambda: svc.connect_fluid(fluid)
        if key == 'imaging':
            if svc.is_emulated():
                return svc.connect_imaging
            design = self._experiment_service.experiment_design or {}
            cfg = configs.assemble_imaging_config(svc.setup, design)
            return lambda: svc.connect_imaging(cfg)
        if key == 'illumination':
            return svc.connect_illumination
        return None

    def _is_connected(self, key):
        return {
            'fluid': self._system_service.fluid_system,
            'imaging': self._system_service.imaging_system,
            'illumination': self._system_service.illumination_system,
        }.get(key) is not None

    def _set_tab_connecting(self, key):
        if key == 'fluid':
            self.fluid_tab.set_status_text("connecting…")
        elif key == 'imaging':
            self.imaging_tab.set_status_text("connecting…")
        elif key == 'illumination':
            self.monet_tab.set_illumination_status("connecting…")

    def _refresh_status(self):
        self.fluid_tab.refresh()
        self.imaging_tab.refresh()
        self.monet_tab.set_illumination_status(
            "connected" if self._is_connected('illumination')
            else "not connected")

    def _mirror_systems(self):
        try:
            self._experiment_service.attach_systems(
                fluid_system=self._system_service.fluid_system,
                imaging_system=self._system_service.imaging_system,
                illumination_system=self._system_service.illumination_system,
            )
        except Exception:
            pass

    # --- run-sequence helpers -----------------------------------------

    def _on_translated(self):
        """After compiling: connect (if needed) + show the Run Sequence tab."""
        self._autoconnect()
        self.tabs.setCurrentWidget(self.run_sequence_tab)

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Run Sequence YAML", "", "YAML files (*.yaml *.yml)")
        if path:
            self.run_sequence_tab.load_protocol_path(path)

    def closeEvent(self, event):
        """Clean shutdown: abort any running experiment, run monet's cleanup,
        release hardware locks."""
        try:
            self._experiment_service.abort()
        except Exception:
            pass
        self.monet_tab.shutdown()
        try:
            self._system_service.close()
        except Exception:
            pass
        super().closeEvent(event)
