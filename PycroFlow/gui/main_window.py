"""PycroFlowMainWindow — the top-level GUI window.

Holds a toolbar of global actions and a tab widget with the experiment,
fluid, imaging, and monet tabs. Owns the :class:`QtBridge` that marshals
ExperimentService observer callbacks onto the GUI thread.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QToolBar, QFileDialog,
)
# Qt6 moved QAction out of QtWidgets into QtGui.
from PyQt6.QtGui import QAction

from PycroFlow.gui.qt_bridge import QtBridge
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

        self.setWindowTitle("PycroFlow")
        self._build_toolbar()
        self._build_tabs()

    def _build_toolbar(self):
        tb = QToolBar("Main")
        self.addToolBar(tb)

        self.act_load = QAction("Load protocol", self)
        self.act_start = QAction("Start", self)
        self.act_pause = QAction("Pause", self)
        self.act_abort = QAction("Abort", self)
        for a in (self.act_load, self.act_start, self.act_pause, self.act_abort):
            tb.addAction(a)

        self.act_load.triggered.connect(self._on_load)
        self.act_start.triggered.connect(lambda: self._experiment_service.start())
        self.act_pause.triggered.connect(lambda: self._experiment_service.pause())
        self.act_abort.triggered.connect(lambda: self._experiment_service.abort())

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.experiment_tab = ExperimentTab(
            self._experiment_service, self._bridge)
        self.fluid_tab = FluidTab(self._system_service)
        self.imaging_tab = ImagingTab(self._system_service)
        self.monet_tab = MonetTab()

        self.tabs.addTab(self.experiment_tab, "Experiment")
        self.tabs.addTab(self.fluid_tab, "Fluid")
        self.tabs.addTab(self.imaging_tab, "Imaging")
        self.tabs.addTab(self.monet_tab, "Monet")
        self.setCentralWidget(self.tabs)

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load protocol YAML", "", "YAML files (*.yaml *.yml)")
        if path:
            self.experiment_tab.load_protocol_path(path)

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
