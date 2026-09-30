"""Webcams tab: set up the fluidics monitoring cameras from the GUI.

A setup/verification surface for WP-FLUIDICS-CAM (not the Phase-1b review/scrub
panel). It edits the setup's ``monitoring:`` block without leaving the app:

* the **save path** clips are written to (blank = the experiment folder),
* the **camera list** -- add/remove cameras and set each one's role, device
  index, and resolution,
* a live low-fps tiled **preview** to confirm the right camera is on the right
  index,

then **Apply** (in-session, for the next run) or **Save to setup file** (writes
the ``monitoring:`` block back to the setup YAML).

The preview grabs frames in a background thread via the same
:class:`~PycroFlow.monitoring.capture_service.CaptureThread` the recorder uses
(the emulator source for an emulated setup, so it works with no camera; the
OpenCV instrument source otherwise). It is disabled while an experiment runs,
because the capture subprocess then owns the cameras.
"""

from __future__ import annotations

import os
from typing import Optional

import yaml
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from PycroFlow import configs
from PycroFlow.monitoring.capture_service import CaptureThread
from PycroFlow.monitoring.config import KNOWN_ROLES, CameraConfig
from PycroFlow.monitoring.tiling import compose, plan_layout

_PREVIEW_FPS = 8


class _PreviewWorker(QThread):
    """Grab + composite frames off the GUI thread; emit the tile as an image.

    Uses the same :class:`~PycroFlow.monitoring.capture_service.CaptureThread`
    the recorder uses, so the preview shows exactly what would be recorded and a
    slow/hung camera can't block the loop (``get_latest`` is non-blocking, so
    ``requestInterruption`` is honoured promptly).
    """

    frame_ready = pyqtSignal(QImage)

    def __init__(
        self, cameras, mode, tile_cols=None, backend=None, parent=None
    ):
        super().__init__(parent)
        self._cameras = cameras
        self._mode = mode
        self._backend = backend
        self._layout = plan_layout(cameras, tile_cols)

    def run(self) -> None:
        threads = [
            CaptureThread(
                c,
                self._mode,
                queue_size=2,
                fps=_PREVIEW_FPS,
                backend=self._backend,
            )
            for c in self._cameras
        ]
        for t in threads:
            t.start()
        interval = int(1000 / max(1, _PREVIEW_FPS))
        try:
            while not self.isInterruptionRequested():
                tile = compose([t.get_latest() for t in threads], self._layout)
                h, w, _ = tile.shape
                img = QImage(
                    tile.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888
                ).copy()  # own the pixels across the thread/signal boundary
                self.frame_ready.emit(img)
                self.msleep(interval)
        finally:
            for t in threads:
                t.stop()
            for t in threads:
                t.join(timeout=1.0)
                t.close()


class WebcamsTab(QWidget):
    def __init__(self, system_service, on_config_changed=None, parent=None):
        super().__init__(parent)
        self._svc = system_service
        self._on_config_changed = on_config_changed
        # One dict per camera row: {"container", "role", "device", "w", "h"}.
        self._rows: list[dict] = []
        self._output_edit: Optional[QLineEdit] = None
        self._worker: Optional[_PreviewWorker] = None
        self._run_locked = False
        self._content: Optional[QWidget] = None
        # Live view (during a run): poll the file the capture process publishes.
        self._live_timer: Optional[QTimer] = None
        self._live_path: Optional[str] = None
        self._root = QVBoxLayout(self)
        self._rebuild()

    # -- construction ----------------------------------------------------
    def _block(self) -> Optional[dict]:
        """The setup's raw ``monitoring`` block (``None`` if no setup)."""
        setup = getattr(self._svc, "setup", None)
        if setup is None:
            return None
        return setup.get("monitoring") or {}

    def _rebuild(self):
        """Rebuild the editor from the current setup's monitoring block.

        All content lives in a single ``_content`` widget that is replaced
        wholesale, so no child (incl. those inside sub-layouts, like the button
        row) is ever left orphaned to the tab.
        """
        self.stop_preview()
        if self._content is not None:
            self._content.setParent(None)
            self._content.deleteLater()
        self._rows = []
        self._content = QWidget()
        layout = QVBoxLayout(self._content)
        self._root.addWidget(self._content)

        block = self._block()
        if block is None:
            layout.addWidget(QLabel("Load a setup to configure webcams."))
            layout.addStretch()
            return

        # -- save path ---------------------------------------------------
        out_box = QGroupBox("Output")
        out_layout = QHBoxLayout(out_box)
        out_layout.addWidget(QLabel("Save clips to:"))
        self._output_edit = QLineEdit(block.get("output_dir") or "")
        self._output_edit.setPlaceholderText(
            "blank = the experiment folder (<save_dir>/fluidics_cam)"
        )
        out_layout.addWidget(self._output_edit, stretch=1)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(self._browse_btn)
        layout.addWidget(out_box)

        # -- cameras -----------------------------------------------------
        self._cams_box = QGroupBox("Cameras")
        self._cams_layout = QVBoxLayout(self._cams_box)
        for cam in block.get("cameras") or []:
            self._add_camera_row(cam)
        self._add_btn = QPushButton("+ Add camera")
        self._add_btn.clicked.connect(lambda: self._add_camera_row())
        self._cams_layout.addWidget(self._add_btn)
        layout.addWidget(self._cams_box)

        # -- actions -----------------------------------------------------
        btns = QHBoxLayout()
        self.preview_btn = QPushButton("Start preview")
        self.preview_btn.clicked.connect(self._toggle_preview)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        self.save_btn = QPushButton("Save to setup file")
        self.save_btn.clicked.connect(self._save)
        for b in (self.preview_btn, self.apply_btn, self.save_btn):
            btns.addWidget(b)
        btns.addStretch()
        layout.addLayout(btns)

        self.preview_label = QLabel("Preview stopped")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(240)
        layout.addWidget(self.preview_label, stretch=1)

        self.set_run_lock(self._run_locked)

    def _add_camera_row(self, cam: Optional[dict] = None):
        """Append an editable camera row (seeded from ``cam`` or defaults)."""
        cam = cam or {}
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel("role"))
        role = QComboBox()
        role.setEditable(True)
        role.addItems(list(KNOWN_ROLES))
        role.setCurrentText(str(cam.get("role", "")) or "sample")
        row.addWidget(role)

        row.addWidget(QLabel("device"))
        device = QSpinBox()
        device.setRange(0, 63)
        try:
            device.setValue(int(cam.get("device", len(self._rows))))
        except (TypeError, ValueError):
            device.setValue(len(self._rows))
        row.addWidget(device)

        row.addWidget(QLabel("size"))
        w = QSpinBox()
        w.setRange(16, 4096)
        w.setValue(int(cam.get("width", 640)))
        h = QSpinBox()
        h.setRange(16, 4096)
        h.setValue(int(cam.get("height", 480)))
        row.addWidget(w)
        row.addWidget(QLabel("x"))
        row.addWidget(h)
        row.addStretch()

        remove = QPushButton("Remove")
        entry = {
            "container": container,
            "role": role,
            "device": device,
            "w": w,
            "h": h,
            "remove": remove,
        }
        remove.clicked.connect(lambda: self._remove_camera_row(entry))
        row.addWidget(remove)

        # Insert above the "+ Add camera" button (always the last widget).
        self._cams_layout.insertWidget(
            self._cams_layout.count() - 1, container
        )
        self._rows.append(entry)
        return entry

    def _remove_camera_row(self, entry: dict):
        if entry in self._rows:
            self._rows.remove(entry)
        entry["container"].setParent(None)
        entry["container"].deleteLater()

    def _browse_output(self):
        start = self._output_edit.text() or ""
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose where to save monitoring clips", start
        )
        if chosen:
            self._output_edit.setText(chosen)

    # -- reading the UI --------------------------------------------------
    def _camera_dicts(self) -> list:
        """The edited cameras as plain dicts (setup/YAML shape)."""
        cams = []
        for i, e in enumerate(self._rows):
            cams.append(
                {
                    "role": e["role"].currentText().strip()
                    or "cam{}".format(i),
                    "device": e["device"].value(),
                    "width": e["w"].value(),
                    "height": e["h"].value(),
                }
            )
        return cams

    def _output_value(self) -> Optional[str]:
        text = self._output_edit.text().strip() if self._output_edit else ""
        return text or None

    def _current_cameras(self):
        """CameraConfigs for the preview, from the edited rows."""
        return [
            CameraConfig(
                role=c["role"],
                device=c["device"],
                width=c["width"],
                height=c["height"],
            )
            for c in self._camera_dicts()
        ]

    def _apply_into(self, block: dict):
        """Write the edited output dir + cameras into a monitoring block."""
        out = self._output_value()
        if out:
            block["output_dir"] = out
        else:
            block.pop("output_dir", None)
        block["cameras"] = self._camera_dicts()

    # -- preview ---------------------------------------------------------
    def _mode(self):
        setup = getattr(self._svc, "setup", None) or {}
        return "emulator" if setup.get("emulated") else "instrument"

    def _toggle_preview(self):
        if self._worker is not None:
            self.stop_preview()
        else:
            self.start_preview()

    def start_preview(self):
        if self._run_locked or self._worker is not None or not self._rows:
            return
        block = self._block() or {}
        self._worker = _PreviewWorker(
            self._current_cameras(),
            self._mode(),
            block.get("tile_cols"),
            backend=block.get("backend"),
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

    # -- apply / save ----------------------------------------------------
    def _apply(self):
        """Apply the edited config to the in-memory setup for the next run."""
        setup = getattr(self._svc, "setup", None)
        if setup is None:
            return
        self._apply_into(setup.setdefault("monitoring", {}))
        restart = self._worker is not None
        self.stop_preview()
        # Rebuild the controller used by the next run from the updated setup.
        if self._on_config_changed is not None:
            self._on_config_changed()
        self._rebuild()
        if restart:
            self.start_preview()

    def _save(self):
        """Write the edited monitoring block back to the setup's YAML file."""
        name = getattr(self._svc, "setup_name", lambda: None)()
        if not name:
            return
        if (
            QMessageBox.question(
                self,
                "Save to setup file",
                "Write the monitoring cameras + output path back to setup "
                "'{}'?\n\nThis rewrites the setup YAML and normalizes its "
                "formatting (comments are not preserved).".format(name),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            path = configs.setup_path(name)
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            self._apply_into(raw.setdefault("monitoring", {}))
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

    # -- live view (during a run) ---------------------------------------
    def _start_live_view(self, path):
        """Poll the capture process's published live-tile file and show it."""
        self._stop_live_view()
        self._live_path = path
        if not path or not hasattr(self, "preview_label"):
            return
        self.preview_label.setText("Live view — waiting for stream…")
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._poll_live)
        self._live_timer.start(200)  # ~5 fps

    def _stop_live_view(self):
        if self._live_timer is not None:
            self._live_timer.stop()
            self._live_timer.deleteLater()
            self._live_timer = None
        self._live_path = None

    def _poll_live(self):
        path = self._live_path
        if not path or not os.path.exists(path):
            return
        img = QImage(path)  # PPM is a core Qt format -> no OpenCV needed
        if img.isNull():
            return
        self._show_frame(img)

    # -- coordinator hooks ----------------------------------------------
    def set_run_lock(self, locked, live_path=None):
        """Lock editing while an experiment owns the cameras.

        The camera preview is stopped (the capture process holds the cameras),
        but if ``live_path`` is given the preview area switches to a live view
        fed by the file the capture process publishes.
        """
        self._run_locked = locked
        if locked:
            self.stop_preview()
            self._start_live_view(live_path)
        else:
            self._stop_live_view()
        widgets = [
            getattr(self, n, None)
            for n in (
                "preview_btn",
                "apply_btn",
                "save_btn",
                "_add_btn",
                "_browse_btn",
                "_output_edit",
            )
        ]
        for e in self._rows:
            widgets += [e["role"], e["device"], e["w"], e["h"], e["remove"]]
        for w in widgets:
            if w is not None:
                w.setEnabled(not locked)

    def refresh(self):
        """Rebuild for the current setup (called on setup change)."""
        self._rebuild()
