"""PycroFlowMainWindow — the top-level GUI window.

A toolbar (setup selector + Connect) over a tab widget: Experiment
Design, Run Sequence, Fluid, Imaging, Monet. The run controls (Load /
Start / Pause-Resume / Abort) live in the Run Sequence tab. The window also
coordinates
hardware connection: the microscope **setup** is chosen in the toolbar, and
subsystems **autoconnect** once an experiment design is loaded (the fluid
system needs the design's reservoir list). Each subsystem tab shows its
connection status and offers a manual Connect/Reconnect, and the status bar
shows a single always-visible confirmation of the loaded setup + each
subsystem's connection state.

Owns the :class:`QtBridge` that marshals ExperimentService observer callbacks
onto the GUI thread.
"""

from PyQt6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QToolBar,
    QLabel,
    QComboBox,
    QMessageBox,
)

# Qt6 moved QAction out of QtWidgets into QtGui.
from PyQt6.QtGui import QAction

from PycroFlow import configs
from PycroFlow.services.experiment_service import ExperimentState
from PycroFlow.gui.qt_bridge import QtBridge
from PycroFlow.gui.widgets.worker import run_in_background
from PycroFlow.gui.tabs.experiment_design_tab import ExperimentDesignTab
from PycroFlow.gui.tabs.experiment_tab import ExperimentTab
from PycroFlow.gui.tabs.fluid_tab import FluidTab
from PycroFlow.gui.tabs.imaging_tab import ImagingTab
from PycroFlow.gui.tabs.monet_tab import MonetTab

# Experiment states during which hardware must not be touched manually (the
# orchestrator owns the instruments).
_RUN_LOCK_STATES = {
    ExperimentState.ORCHESTRATING,
    ExperimentState.RUNNING,
    ExperimentState.PAUSED,
}


class PycroFlowMainWindow(QMainWindow):
    def __init__(self, experiment_service, system_service, parent=None):
        super().__init__(parent)
        self._experiment_service = experiment_service
        self._system_service = system_service
        self._bridge = QtBridge(experiment_service, parent=self)
        # Keys of subsystems whose connect is currently in flight.
        self._connecting = set()

        from PycroFlow import __version__

        self.setWindowTitle("PycroFlow {}".format(__version__))
        # Open wide enough for the Fluid tab's wiring schematic (24-port grid
        # + pumps) to show without the user having to widen the window first.
        self.resize(1280, 820)
        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()
        self._init_setup()

    # --- toolbar ------------------------------------------------------

    def _build_toolbar(self):
        # Hardware connection only — the run controls (Load / Start /
        # Pause-Resume / Abort) live in the Run Sequence tab.
        tb = QToolBar("Main")
        self.addToolBar(tb)

        tb.addWidget(QLabel("Setup: "))
        self.setup_combo = QComboBox()
        self.setup_combo.addItems(configs.list_setups())
        tb.addWidget(self.setup_combo)
        self.act_connect = QAction("Connect", self)
        tb.addAction(self.act_connect)
        self.act_disconnect = QAction("Disconnect", self)
        tb.addAction(self.act_disconnect)

        self.setup_combo.currentTextChanged.connect(self._on_setup_changed)
        self.act_connect.triggered.connect(self._reconnect_all)
        self.act_disconnect.triggered.connect(self._disconnect_all)

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.design_tab = ExperimentDesignTab(
            self._experiment_service,
            on_translated=self._on_translated,
            on_design_loaded=self._on_design_changed,
            reservoir_ids_provider=self._system_service.reservoir_ids,
            laser_options_provider=self._system_service.laser_options,
        )
        self.run_sequence_tab = ExperimentTab(
            self._experiment_service, self._bridge
        )
        self.fluid_tab = FluidTab(
            self._system_service,
            on_connect=lambda: self._connect_system("fluid"),
        )
        self.imaging_tab = ImagingTab(
            self._system_service,
            on_connect=lambda: self._connect_system("imaging"),
        )
        self.monet_tab = MonetTab()

        self.tabs.addTab(self.design_tab, "Experiment Design")
        self.tabs.addTab(self.run_sequence_tab, "Run Sequence")
        self.tabs.addTab(self.fluid_tab, "Fluid")
        self.tabs.addTab(self.imaging_tab, "Imaging")
        self.tabs.addTab(self.monet_tab, "Monet")
        self.setCentralWidget(self.tabs)

        # Lock manual hardware access (setup/connect, fluid manual controls,
        # the embedded monet GUI) while the orchestrator owns the instruments.
        self._bridge.state_changed.connect(self._on_experiment_state)

    def _build_statusbar(self):
        # A single always-visible confirmation of the loaded setup and each
        # subsystem's connection state (the per-tab labels stay too).
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label)

    # --- setup / connection -------------------------------------------

    def _init_setup(self):
        if self.setup_combo.count():
            # Selecting index 0 fires currentTextChanged -> _on_setup_changed
            # unless it's already current; load explicitly to be sure.
            self._on_setup_changed(self.setup_combo.currentText())

    def _on_setup_changed(self, name):
        if not name:
            return
        # Switching setups: release any existing connections first, so the
        # live hardware can never disagree with the selected setup. Disconnect
        # before load_setup so each subsystem is released in its own setup's
        # context (e.g. the emulated-serial wrapper for an emulated setup).
        self._system_service.disconnect_all()
        self._mirror_systems()
        try:
            self._system_service.load_setup(name)
        except Exception as exc:
            QMessageBox.critical(self, "Setup load failed", "{}".format(exc))
            self._refresh_status()
            return
        self.monet_tab.set_setup(self._system_service.get_monet_setup())
        # The setup supplies the design editor's reservoir-id and laser
        # dropdown options; refresh them for the setup just loaded.
        self.design_tab.refresh_setup_options()
        self._refresh_status()
        # If a design is already loaded, connect for the new setup.
        if self._experiment_service.experiment_design:
            self._autoconnect()

    def _on_design_changed(self):
        # A freshly loaded design gets clean connections: reconnect every
        # subsystem (which disconnects first), so any prior design's hardware
        # handles are released before the new ones are opened.
        self._reconnect_all()

    def _on_experiment_state(self, old, new):
        """Lock/unlock manual hardware access on experiment state changes."""
        self._lock_hardware(new in _RUN_LOCK_STATES)

    def _lock_hardware(self, locked):
        self.setup_combo.setEnabled(not locked)
        self.act_connect.setEnabled(not locked)
        self.act_disconnect.setEnabled(not locked)
        self.fluid_tab.set_run_lock(locked)
        self.imaging_tab.set_run_lock(locked)
        self.monet_tab.set_run_lock(locked)
        if not locked:
            # Restore real connection statuses after the run lock lifts.
            self._refresh_status()

    def _autoconnect(self):
        if self._system_service.setup is None:
            return
        for key in ("illumination", "imaging", "fluid"):
            if not self._is_connected(key):
                self._connect_system(key, warn_missing=False)

    def _reconnect_all(self):
        """(Re)connect every subsystem for the current setup.

        Used by the toolbar Connect button and on design load. Unlike
        :meth:`_autoconnect`, it does *not* skip already-connected subsystems
        — each :meth:`_connect_system` disconnects first — so switching setups
        or loading a new design re-targets the hardware with fresh connections
        rather than leaving the previous ones attached.
        """
        if self._system_service.setup is None:
            QMessageBox.warning(
                self, "No setup", "Select a microscope setup first."
            )
            return
        for key in ("illumination", "imaging", "fluid"):
            self._connect_system(key, warn_missing=False)

    def _disconnect_all(self):
        """Toolbar Disconnect: release every subsystem's connection."""
        self._system_service.disconnect_all()
        self._mirror_systems()
        self._refresh_status()

    def _connect_system(self, key, warn_missing=True):
        if self._system_service.setup is None:
            if warn_missing:
                QMessageBox.warning(
                    self, "No setup", "Select a microscope setup first."
                )
            return
        if key in self._connecting:
            return
        call = self._connect_call(key, warn_missing)
        if call is None:
            return
        # Free any existing connection for this subsystem before reconnecting,
        # so loading a new design / switching setups never collides with a
        # stale, still-open hardware handle.
        self._system_service.disconnect(key)
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
                self,
                "Connection failed",
                "Could not connect the {} system:\n\n{!r}".format(key, exc),
            )

        # Imaging touches the Micro-Manager Core (pycromanager/ZMQ): keep it
        # on the GUI thread. Fluid (serial) and illumination run in the
        # background so the UI stays responsive.
        if key == "imaging":
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
        if key == "fluid":
            design = self._experiment_service.experiment_design
            fluid = (design or {}).get("fluid")
            if not fluid or not fluid.get("settings"):
                if warn_missing:
                    QMessageBox.warning(
                        self,
                        "No experiment design",
                        "Load an experiment design first — the fluid system "
                        "needs its reservoir list.",
                    )
                return None
            return lambda: svc.connect_fluid(fluid)
        if key == "imaging":
            if svc.is_emulated():
                return svc.connect_imaging
            design = self._experiment_service.experiment_design or {}
            cfg = configs.assemble_imaging_config(svc.setup, design)
            return lambda: svc.connect_imaging(cfg)
        if key == "illumination":
            return svc.connect_illumination
        return None

    def _is_connected(self, key):
        return {
            "fluid": self._system_service.fluid_system,
            "imaging": self._system_service.imaging_system,
            "illumination": self._system_service.illumination_system,
        }.get(key) is not None

    def _set_tab_connecting(self, key):
        if key == "fluid":
            self.fluid_tab.set_status_text("connecting…")
        elif key == "imaging":
            self.imaging_tab.set_status_text("connecting…")
        elif key == "illumination":
            self.monet_tab.set_illumination_status("connecting…")
        self._update_statusbar()

    def _refresh_status(self):
        self.fluid_tab.refresh()
        self.imaging_tab.refresh()
        self.monet_tab.set_illumination_status(
            "connected"
            if self._is_connected("illumination")
            else "not connected"
        )
        self._update_statusbar()

    def _status_word(self, key):
        if key in self._connecting:
            return "connecting…"
        return "✓ connected" if self._is_connected(key) else "✗ not connected"

    def _update_statusbar(self):
        """Refresh the status bar's setup + per-subsystem confirmation."""
        if self._system_service.setup is None:
            self.status_label.setText("No setup loaded")
            return
        parts = ["Setup: {}".format(self.setup_combo.currentText())]
        if self._system_service.is_emulated():
            parts[0] += " (emulated)"
        for key, label in (
            ("fluid", "Fluid"),
            ("imaging", "Imaging"),
            ("illumination", "Illumination"),
        ):
            parts.append("{}: {}".format(label, self._status_word(key)))
        self.status_label.setText("      ".join(parts))

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
        """After compiling: connect (if needed) + show the Run Sequence tab.

        Translating is the point where the edited design becomes the thing
        that will run, so the connected fluid system's reservoirs are
        re-synced from it — editing the reservoir table after connecting
        would otherwise leave the hardware routing by the old list.
        """
        self._autoconnect()
        self._sync_fluid_design()
        self.tabs.setCurrentWidget(self.run_sequence_tab)

    def _sync_fluid_design(self):
        """Push the current design's reservoirs into the connected system."""
        design = self._experiment_service.experiment_design or {}
        fluid = design.get('fluid')
        if not fluid:
            return
        try:
            self._system_service.sync_fluid_reservoirs(fluid)
        except Exception as exc:
            QMessageBox.warning(
                self, "Reservoirs not applied",
                "The design's reservoirs could not be applied to the "
                "connected fluid system:\n\n{!r}\n\nReconnect the fluid "
                "system before starting.".format(exc))

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
