"""Webcams tab: verify the fluidics monitoring cameras and set device indices.

A setup/verification surface for WP-FLUIDICS-CAM (not the Phase-1b review/scrub
panel): it shows the setup's monitoring cameras, lets you edit each camera's
device index, gives a live low-fps tiled preview to confirm the right camera is
on the right index, and writes changes back to the setup YAML.

The preview grabs frames in a background thread via the same
:mod:`PycroFlow.monitoring.sources` used by the capture service (the emulator
source for an emulated setup, so it works with no camera; the OpenCV instrument
source otherwise). It is disabled while an experiment runs, because the capture
subprocess then owns the cameras.
"""

from __future__ import annotations

from typing import Optional

import yaml
from loguru import logger
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from PycroFlow import configs
from PycroFlow.monitoring.config import CameraConfig, load_monitoring_config
from PycroFlow.monitoring.sources import make_source
from PycroFlow.monitoring.tiling import compose, plan_layout

_PREVIEW_FPS = 8


class _PreviewWorker(QThread):
    """Grab + composite frames off the GUI thread; emit the tile as an image."""

    frame_ready = pyqtSignal(QImage)

    def __init__(self, cameras, mode, tile_cols=None, parent=None):
        super().__init__(parent)
        self._cameras = cameras
        self._mode = mode
        self._layout = plan_layout(cameras, tile_cols)

    def run(self) -> None:
        sources = [make_source(c, self._mode) for c in self._cameras]
        for s in sources:
            try:
                s.open()
            except (
                Exception
            ) as exc:  # a down camera -> black panel, not a stop
                logger.info(
                    "monitoring preview: {} did not open ({!r})",
                    s.camera.role,
                    exc,
                )
        interval = int(1000 / max(1, _PREVIEW_FPS))
        try:
            while not self.isInterruptionRequested():
                frames = []
                for s in sources:
                    try:
                        frames.append(s.read())
                    except Exception:
                        frames.append(None)
                tile = compose(frames, self._layout)
                h, w, _ = tile.shape
                img = QImage(
                    tile.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888
                )
                self.frame_ready.emit(img)
                self.msleep(interval)
        finally:
            for s in sources:
                s.close()


class WebcamsTab(QWidget):
    def __init__(self, system_service, on_config_changed=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._on_config_changed = on_config_changed
        self._config = None
        self._spins: list[QSpinBox] = []
        self._worker: Optional[_PreviewWorker] = None
        self._run_locked = False
        self._root = QVBoxLayout(self)
        self._rebuild()

    # -- construction ----------------------------------------------------
    def _rebuild(self):
        """Rebuild the tab for the current setup's monitoring config."""
        self.stop_preview()
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._spins = []

        setup = getattr(self._svc, "setup", None)
        self._config = load_monitoring_config(setup) if setup else None
        if self._config is None:
            self._root.addWidget(
                QLabel(
                    "This setup declares no monitoring cameras.\n"
                    "Add a 'monitoring:' block with cameras to the setup YAML."
                )
            )
            self._root.addStretch()
            return

        cams_box = QGroupBox("Cameras")
        cams_layout = QVBoxLayout(cams_box)
        for cam in self._config.cameras:
            row = QHBoxLayout()
            row.addWidget(QLabel("{} — device".format(cam.role)))
            spin = QSpinBox()
            spin.setRange(0, 63)
            try:
                spin.setValue(int(cam.device))
            except (TypeError, ValueError):
                spin.setValue(0)
            row.addWidget(spin)
            row.addWidget(QLabel("{}x{}".format(cam.width, cam.height)))
            row.addStretch()
            self._spins.append(spin)
            cams_layout.addLayout(row)
        self._root.addWidget(cams_box)

        btns = QHBoxLayout()
        self.preview_btn = QPushButton("Start preview")
        self.preview_btn.clicked.connect(self._toggle_preview)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        self.save_btn = QPushButton("Save to setup file")
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.preview_btn)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.save_btn)
        btns.addStretch()
        self._root.addLayout(btns)

        self.preview_label = QLabel("Preview stopped")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(240)
        self._root.addWidget(self.preview_label, stretch=1)

        self.set_run_lock(self._run_locked)

    # -- preview ---------------------------------------------------------
    def _current_cameras(self):
        """CameraConfigs with device indices taken from the spinboxes."""
        cams = []
        for spin, cam in zip(self._spins, self._config.cameras):
            cams.append(
                CameraConfig(
                    role=cam.role,
                    device=spin.value(),
                    width=cam.width,
                    height=cam.height,
                )
            )
        return cams

    def _mode(self):
        setup = getattr(self._svc, "setup", None) or {}
        return "emulator" if setup.get("emulated") else "instrument"

    def _toggle_preview(self):
        if self._worker is not None:
            self.stop_preview()
        else:
            self.start_preview()

    def start_preview(self):
        if (
            self._config is None
            or self._run_locked
            or self._worker is not None
        ):
            return
        self._worker = _PreviewWorker(
            self._current_cameras(), self._mode(), self._config.tile_cols
        )
        self._worker.frame_ready.connect(self._show_frame)
        self._worker.start()
        self.preview_btn.setText("Stop preview")

    def stop_preview(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.requestInterruption()
            worker.wait(2000)
        if hasattr(self, "preview_btn"):
            self.preview_btn.setText("Start preview")
        if hasattr(self, "preview_label"):
            self.preview_label.setText("Preview stopped")

    def _show_frame(self, img: QImage):
        pix = QPixmap.fromImage(img)
        self.preview_label.setPixmap(
            pix.scaled(
                self.preview_label.width(),
                self.preview_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # -- edits -----------------------------------------------------------
    def _write_devices_into(self, cameras_list):
        """Copy the spinbox device values into a cameras list (in place)."""
        for i, spin in enumerate(self._spins):
            if i < len(cameras_list):
                cameras_list[i]["device"] = spin.value()

    def _apply(self):
        """Apply device indices to the in-memory setup for the next run."""
        setup = getattr(self._svc, "setup", None) or {}
        cams = (setup.get("monitoring") or {}).get("cameras")
        if not cams:
            return
        self._write_devices_into(cams)
        restart = self._worker is not None
        self.stop_preview()
        # Reflect the change in the controller used by the next run.
        if self._on_config_changed is not None:
            self._on_config_changed()
        self._rebuild()
        if restart:
            self.start_preview()

    def _save(self):
        """Write the device indices back to the setup's YAML file."""
        name = getattr(self._svc, "setup_name", lambda: None)()
        if not name:
            return
        if (
            QMessageBox.question(
                self,
                "Save to setup file",
                "Write the camera device indices back to setup '{}'?\n\n"
                "This rewrites the setup YAML and normalizes its "
                "formatting (comments are not preserved).".format(name),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            path = configs.setup_path(name)
            with open(path) as f:
                raw = yaml.safe_load(f)
            cams = (raw.get("monitoring") or {}).get("cameras")
            if not cams:
                raise ValueError("setup has no monitoring.cameras block")
            self._write_devices_into(cams)
            with open(path, "w") as f:
                yaml.safe_dump(raw, f, sort_keys=False)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Save failed",
                "Could not write the setup: {}".format(exc),
            )
            return
        self._apply()  # also apply in-session so the next run matches the file

    # -- coordinator hooks ----------------------------------------------
    def set_run_lock(self, locked):
        """Disable editing + preview while an experiment owns the cameras."""
        self._run_locked = locked
        if locked:
            self.stop_preview()
        for w in getattr(self, "_spins", []):
            w.setEnabled(not locked)
        for name in ("preview_btn", "apply_btn", "save_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not locked)

    def refresh(self):
        """Rebuild for the current setup (called on setup change)."""
        self._rebuild()
