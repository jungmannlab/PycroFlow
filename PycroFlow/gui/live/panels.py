"""The fixed operator panels: sidebar QC-at-a-glance + Overview/Zoom.

Both are :class:`~PycroFlow.gui.live.contribution.StreamConsumer`\\ s — the shell
connects the client's ``update`` signal to their :meth:`on_update`, and they
render from the seam payloads (metrics snapshot + thumbnail + state), never
computing anything themselves (C31: each panel subscribes to the stream, it is
not a compute owner).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.gui.live.advisor import (
    SEVERITY_COLOR,
    FindingsAdapter,
    worst_severity,
)

# The ~15 colour-coded QC-at-a-glance metrics (C31). Each row: the metric key we
# read from the seam metrics snapshot (None = not yet on the live seam, shown as
# a placeholder with a db-range tooltip), the display label, the unit, and a
# coarse target hint used only for the tooltip (real per-cohort ranges come from
# the registry / WP-3 later — TODO below).
QC_METRICS = [
    ("nena_nm", "NeNA", "nm", "lower is better"),
    ("spots_per_frame", "locs/frame", "", "target band"),
    ("background", "Background", "", "lower is better"),
    ("n_locs", "Localizations", "", "grows with frames"),
    ("photons", "Photons", "", "higher is better"),
    ("frc_nm", "FRC", "nm", "lower is better"),
    ("sbr", "SBR", "", "higher is better"),
    ("overlap", "Overlap", "", "lower is better"),
    ("duty_cycle", "Duty cycle", "", "target band"),
    ("unspecific", "Unspecific", "", "lower is better"),
    ("on_off_ratio", "On/Off", "", "target band"),
    ("c_imager_nM", "c_imager", "nM", "as designed"),
    ("density", "Density", "/um^2", "target band"),
    ("loc_density", "Loc density", "/um^2", "target band"),
    ("drift_nm", "Drift", "nm", "lower is better"),
]

# TODO(WP-3): replace the static "hint" tooltip text with your-database range
# tooltips read via the registry client (db_range(metric, cohort)); the brief's
# "your-database-range tooltip" per metric. The seam already carries the metric
# values; only the target band needs the registry read.


def _swatch(color: str, diameter: int = 14) -> QLabel:
    lab = QLabel()
    lab.setFixedSize(diameter, diameter)
    lab.setStyleSheet(
        "border-radius: {}px; background: {};".format(diameter // 2, color)
    )
    return lab


class QcAtAGlance(QWidget):
    """Fixed left sidebar: ~15 colour-coded metrics + advisor light + controls.

    Subscribes to the stream: on each ``metrics`` update it refreshes the metric
    values and re-runs the advisor (via the injected :class:`Advisor`) to set the
    traffic-light + core-control availability. Emits :attr:`estimate_requested`
    and :attr:`undrift_requested` etc. so the shell / operator module can wire
    them to real actions (WP-ADVISOR's estimate; the service's undrift).
    """

    estimate_requested = pyqtSignal()
    undrift_requested = pyqtSignal()
    filter_preview_requested = pyqtSignal()
    abort_requested = pyqtSignal()
    params_changed = pyqtSignal(dict)

    def __init__(self, advisor=None, parent=None):
        super().__init__(parent)
        self._advisor = advisor
        self._value_labels: dict = {}
        self._swatches: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        # Advisor traffic-light.
        light_box = QGroupBox("Advisor")
        lb = QHBoxLayout(light_box)
        self.advisor_light = _swatch(SEVERITY_COLOR["ok"], 18)
        self.advisor_text = QLabel("all clear")
        lb.addWidget(self.advisor_light)
        lb.addWidget(self.advisor_text)
        lb.addStretch()
        outer.addWidget(light_box)

        # QC-at-a-glance metric grid.
        qc_box = QGroupBox("QC at a glance")
        grid = QGridLayout(qc_box)
        grid.setColumnStretch(2, 1)
        for row, (key, label, unit, hint) in enumerate(QC_METRICS):
            sw = _swatch("#888")
            name = QLabel(label)
            value = QLabel("—")
            value.setObjectName("qc_{}".format(key))
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            tip = "target: {}".format(hint)
            if unit:
                tip += "  ·  unit: {}".format(unit)
            tip += "\n(db-range tooltip TODO: WP-3 registry read)"
            for w in (name, value):
                w.setToolTip(tip)
            grid.addWidget(sw, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(value, row, 2)
            self._value_labels[key] = value
            self._swatches[key] = sw
        outer.addWidget(qc_box)

        # Core controls.
        ctl_box = QGroupBox("Core controls")
        form = QFormLayout(ctl_box)
        self.box_size = QSpinBox()
        self.box_size.setRange(3, 51)
        self.box_size.setSingleStep(2)
        self.box_size.setValue(7)
        self.min_gradient = QSpinBox()
        self.min_gradient.setRange(0, 100000)
        self.min_gradient.setValue(5000)
        self.blur = QDoubleSpinBox()
        self.blur.setRange(0.0, 10.0)
        self.blur.setSingleStep(0.1)
        for w in (self.box_size, self.min_gradient, self.blur):
            w.valueChanged.connect(self._emit_params)
        form.addRow("Box size", self.box_size)

        grad_row = QHBoxLayout()
        grad_row.addWidget(self.min_gradient)
        self.estimate_btn = QPushButton("estimate")
        self.estimate_btn.setToolTip(
            "Estimate min. net gradient from data (WP-ADVISOR)"
        )
        self.estimate_btn.clicked.connect(self.estimate_requested)
        grad_row.addWidget(self.estimate_btn)
        grad_w = QWidget()
        grad_w.setLayout(grad_row)
        form.addRow("Min. net gradient", grad_w)
        form.addRow("Blur", self.blur)

        btn_row = QHBoxLayout()
        self.undrift_btn = QPushButton("Undrift now")
        self.undrift_btn.clicked.connect(self.undrift_requested)
        self.filter_btn = QPushButton("Filter preview")
        self.filter_btn.clicked.connect(self.filter_preview_requested)
        btn_row.addWidget(self.undrift_btn)
        btn_row.addWidget(self.filter_btn)
        btn_w = QWidget()
        btn_w.setLayout(btn_row)
        form.addRow(btn_w)

        self.abort_btn = QPushButton("Early-abort")
        self.abort_btn.setStyleSheet("color: #d9534f; font-weight: bold;")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.abort_requested)
        form.addRow(self.abort_btn)
        outer.addWidget(ctl_box)

        outer.addStretch()

    def _emit_params(self, *_: object) -> None:
        self.params_changed.emit(self.current_params())

    def current_params(self) -> dict:
        return {
            "Box Size": self.box_size.value(),
            "Min. Net Gradient": self.min_gradient.value(),
            "Blur": self.blur.value(),
        }

    # -- stream ------------------------------------------------------------
    def on_update(self, update) -> None:
        if update.kind == "metrics":
            self._on_metrics(update.payload.get("metrics", {}) or {})
        elif update.kind == "state":
            self._on_state(update.payload.get("state", ""))

    def _on_state(self, state: str) -> None:
        self.abort_btn.setEnabled(
            state in ("fov_started", "experiment_started")
        )

    def _on_metrics(self, metrics: dict) -> None:
        for key, label in self._value_labels.items():
            val = metrics.get(key)
            if val is None:
                label.setText("—")
                self._swatches[key].setStyleSheet(
                    "border-radius: 7px; background: #888;"
                )
                continue
            if isinstance(val, float):
                label.setText("{:.3g}".format(val))
            else:
                label.setText(str(val))
            self._swatches[key].setStyleSheet(
                "border-radius: 7px; background: {};".format(
                    SEVERITY_COLOR["ok"]
                )
            )
        self._refresh_advisor(metrics)

    def _refresh_advisor(self, metrics: dict) -> None:
        if self._advisor is None:
            return
        try:
            findings = FindingsAdapter.coerce_all(
                self._advisor.findings_for(metrics)
            )
        except Exception:
            return
        sev = worst_severity(findings)
        self.advisor_light.setStyleSheet(
            "border-radius: 9px; background: {};".format(SEVERITY_COLOR[sev])
        )
        if findings:
            self.advisor_text.setText(findings[0].message)
            self.advisor_text.setToolTip(
                "\n".join(
                    "[{}] {}{}".format(
                        f.severity,
                        f.message,
                        "  ->  " + f.suggestion if f.suggestion else "",
                    )
                    for f in findings
                )
            )
        else:
            self.advisor_text.setText("all clear")
            self.advisor_text.setToolTip("")


class OverviewZoom(QWidget):
    """Always-visible Overview + Zoom: contrast, magnifier, pop-out.

    Renders the thumbnail carried on the ``thumbnail`` seam kind. Two-handle
    contrast + Auto (display-only, applied to the shown image), a display-only
    magnifier readout, a scale bar label, and a double-click pop-out. Drag-ROI
    and click-to-move-zoom are stubbed as display-only interactions here (the
    zoomed re-render round-trips through the service in a later WP-4 run).
    """

    popout_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        head = QHBoxLayout()
        head.addWidget(QLabel("Overview / Zoom"))
        head.addStretch()
        self.auto_btn = QPushButton("Auto")
        self.auto_btn.setToolTip("Auto-contrast the displayed frame")
        self.auto_btn.clicked.connect(self._auto_contrast)
        head.addWidget(self.auto_btn)
        layout.addLayout(head)

        self.image = QLabel("no frame yet")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(160)
        self.image.setFrameShape(QFrame.Shape.Box)
        self.image.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.image, 1)

        # Two-handle contrast (min/max display window).
        contrast = QHBoxLayout()
        contrast.addWidget(QLabel("black"))
        self.black = QSpinBox()
        self.black.setRange(0, 65535)
        self.white = QSpinBox()
        self.white.setRange(0, 65535)
        self.white.setValue(65535)
        for w in (self.black, self.white):
            w.valueChanged.connect(self._reapply_contrast)
        contrast.addWidget(self.black)
        contrast.addWidget(QLabel("white"))
        contrast.addWidget(self.white)
        contrast.addStretch()
        layout.addLayout(contrast)

        foot = QHBoxLayout()
        self.magnifier = QLabel("magnifier: —")
        self.scale_bar = QLabel("scale: —")
        foot.addWidget(self.magnifier)
        foot.addStretch()
        foot.addWidget(self.scale_bar)
        layout.addLayout(foot)

    # -- stream ------------------------------------------------------------
    def on_update(self, update) -> None:
        if update.kind == "thumbnail":
            self._on_thumbnail(update.payload)

    def _on_thumbnail(self, payload: dict) -> None:
        shape = payload.get("shape")
        # The seam may carry raw bytes ("data" + "shape" + "dtype") or, in the
        # minimal WP-4 slice, only a shape. Render bytes when present.
        data = payload.get("data")
        if data is not None and shape is not None:
            self._set_image_from_bytes(data, shape, payload.get("dtype"))
        elif shape is not None:
            self.image.setText("frame {}".format(shape))
        pixelsize = payload.get("pixelsize_nm")
        if pixelsize and shape is not None:
            self.scale_bar.setText(
                "scale: {:.0f} nm/px".format(float(pixelsize))
            )

    def _set_image_from_bytes(self, data, shape, dtype) -> None:
        try:
            import numpy as np

            arr = np.frombuffer(bytes(data), dtype=dtype or np.uint16)
            arr = arr.reshape(shape[:2])
            self._raw = arr
            self._render()
        except Exception:
            self.image.setText("frame {}".format(shape))

    def _render(self) -> None:
        raw = getattr(self, "_raw", None)
        if raw is None:
            return
        import numpy as np

        lo, hi = self.black.value(), max(
            self.white.value(), self.black.value() + 1
        )
        clipped = np.clip((raw.astype("float32") - lo) / (hi - lo), 0, 1)
        img8 = (clipped * 255).astype("uint8")
        h, w = img8.shape
        qimg = QImage(img8.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
        self._pixmap = QPixmap.fromImage(qimg)
        self.image.setPixmap(
            self._pixmap.scaled(
                self.image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.magnifier.setText("magnifier: {}x{}".format(w, h))

    def _auto_contrast(self) -> None:
        raw = getattr(self, "_raw", None)
        if raw is None:
            return
        import numpy as np

        self.black.setValue(int(np.percentile(raw, 1)))
        self.white.setValue(int(np.percentile(raw, 99)))
        self._render()

    def _reapply_contrast(self, *_: object) -> None:
        if getattr(self, "_raw", None) is not None:
            self._render()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.popout_requested.emit()
        super().mouseDoubleClickEvent(event)


__all__ = ["QcAtAGlance", "OverviewZoom", "QC_METRICS"]
