"""Quality tab: the FIRST client over the live-analysis streaming seam.

Minimal by design — this is the streaming seam + a minimal client, not the full
V0.8 Quality UI (that is WP-GUI). It subscribes to a
:class:`~PycroFlow.live_analysis.client_seam.LiveAnalysisClient` fed by the
headless :class:`~PycroFlow.live_analysis.service.LiveAnalysisService`, shows the
live localization metrics (NeNA, localizations/frame, background) + a compact
trend, live lag (queue depth), a thumbnail slot, and an **early-abort** button
that issues the service's control call.

Because the service runs the pipeline on worker threads/processes, updates arrive
off the GUI thread; :class:`QualityUpdateBridge` re-emits them as Qt signals so
every widget touch happens on the GUI thread (same pattern as ``qt_bridge``).
Qt is imported lazily and the module runs headless (``QT_QPA_PLATFORM=offscreen``).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.live_analysis.client_seam import CallbackClient, LiveUpdate


class QualityUpdateBridge(QObject):
    """Re-emits :class:`LiveUpdate`\\ s as Qt signals on the GUI thread.

    Register :attr:`client` with the service's :class:`UpdateHub`; the hub calls
    :meth:`_on_update` from a worker thread and the signal marshals onto the GUI
    event loop (default ``AutoConnection``), so slots run on the GUI thread.
    """

    update = pyqtSignal(object)  # LiveUpdate

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = CallbackClient(self._on_update)

    def _on_update(self, update: LiveUpdate) -> None:
        self.update.emit(update)


class QualityTab(QWidget):
    """Live localization quality view + early-abort control (first seam client).

    Parameters
    ----------
    service : LiveAnalysisService or None
        The headless service. Its ``request_abort`` is the control call the
        abort button issues; its ``hub`` is the update channel we subscribe to.
        None is allowed (a passive view) for construction in tests.
    """

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._bridge = QualityUpdateBridge(self)
        self._run_id = None
        self._trend: list[float] = []
        self._build_ui()
        self._bridge.update.connect(self._on_update)
        if service is not None:
            service.hub.add(self._bridge.client)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        head.addWidget(QLabel("run_id:"))
        self.run_id_label = QLabel("—")
        self.run_id_label.setObjectName("run_id_label")
        head.addWidget(self.run_id_label)
        head.addStretch()
        self.state_label = QLabel("idle")
        head.addWidget(QLabel("state:"))
        head.addWidget(self.state_label)
        layout.addLayout(head)

        metrics_box = QGroupBox("Live metrics")
        m = QVBoxLayout(metrics_box)
        self.nena_label = QLabel("NeNA: —")
        self.locs_label = QLabel("localizations/frame: —")
        self.bg_label = QLabel("background: —")
        self.nlocs_label = QLabel("n_locs: —  ·  frames: —")
        for w in (
            self.nena_label,
            self.locs_label,
            self.bg_label,
            self.nlocs_label,
        ):
            m.addWidget(w)
        layout.addWidget(metrics_box)

        # Compact NeNA trend as a text sparkline (a real plot is WP-GUI).
        trend_box = QGroupBox("NeNA trend")
        t = QVBoxLayout(trend_box)
        self.trend_label = QLabel("—")
        self.trend_label.setStyleSheet("font-family: monospace;")
        t.addWidget(self.trend_label)
        layout.addWidget(trend_box)

        lag_box = QGroupBox("Pipeline lag (bounded queue)")
        lag_row = QHBoxLayout(lag_box)
        self.lag_bar = QProgressBar()
        self.lag_bar.setRange(0, 100)
        self.lag_bar.setFormat("%p% full")
        lag_row.addWidget(self.lag_bar)
        self.lag_label = QLabel("0/0")
        lag_row.addWidget(self.lag_label)
        layout.addWidget(lag_box)

        # Thumbnail slot (live render placeholder — the seam carries it).
        thumb_box = QGroupBox("Live view")
        th = QVBoxLayout(thumb_box)
        self.thumb_label = QLabel("no frame yet")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setMinimumHeight(80)
        th.addWidget(self.thumb_label)
        layout.addWidget(thumb_box)

        controls = QHBoxLayout()
        self.abort_btn = QPushButton("Early-abort")
        self.abort_btn.clicked.connect(self._on_abort)
        controls.addWidget(self.abort_btn)
        controls.addStretch()
        layout.addLayout(controls)

        log_box = QGroupBox("Log")
        lg = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(90)
        lg.addWidget(self.log_view)
        layout.addWidget(log_box)

    # -- control call ------------------------------------------------------
    def _on_abort(self) -> None:
        if self._service is not None:
            self._service.request_abort()

    # -- update channel (runs on the GUI thread via the bridge) ------------
    def _on_update(self, update: LiveUpdate) -> None:
        if update.run_id:
            self._run_id = update.run_id
            self.run_id_label.setText(update.run_id)
        if update.kind == "state":
            self._on_state(update.payload)
        elif update.kind == "metrics":
            self._on_metrics(update.payload)
        elif update.kind == "thumbnail":
            self._on_thumbnail(update.payload)
        elif update.kind == "record":
            self.log_view.appendPlainText(
                "record posted: {}".format(update.payload.get("posted"))
            )
        elif update.kind == "log":
            self.log_view.appendPlainText(
                str(update.payload.get("message", ""))
            )

    def _on_state(self, payload: dict) -> None:
        state = payload.get("state", "")
        self.state_label.setText(state)
        self.log_view.appendPlainText("state: {}".format(state))
        # Abort is only meaningful while a FOV is live.
        self.abort_btn.setEnabled(
            state in ("fov_started", "experiment_started")
        )

    def _on_metrics(self, payload: dict) -> None:
        metrics = payload.get("metrics", {}) or {}
        nena = metrics.get("nena_nm")
        self.nena_label.setText(
            "NeNA: {} nm".format(nena) if nena is not None else "NeNA: —"
        )
        spf = metrics.get("spots_per_frame")
        self.locs_label.setText(
            "localizations/frame: {}".format(spf)
            if spf is not None
            else "localizations/frame: —"
        )
        bg = metrics.get("background")
        self.bg_label.setText(
            "background: {}".format(bg) if bg is not None else "background: —"
        )
        self.nlocs_label.setText(
            "n_locs: {}  ·  frames: {}".format(
                metrics.get("n_locs"), metrics.get("n_frames")
            )
        )
        if nena is not None:
            self._trend.append(float(nena))
            self.trend_label.setText(self._sparkline(self._trend))

        backend = payload.get("backend", {}) or {}
        depth = backend.get("queue_depth", 0) or 0
        size = backend.get("queue_size", 0) or 0
        self.lag_bar.setValue(int(100 * depth / size) if size else 0)
        self.lag_label.setText("{}/{}".format(depth, size))

    def _on_thumbnail(self, payload: dict) -> None:
        # Minimal seam wiring: report a thumbnail arrived (rendering is WP-GUI).
        shape = payload.get("shape")
        self.thumb_label.setText(
            "frame {}".format(shape) if shape else "frame received"
        )

    @staticmethod
    def _sparkline(values: list[float]) -> str:
        """A tiny unicode sparkline of the recent NeNA values (no plot dep)."""
        if not values:
            return "—"
        recent = values[-40:]
        lo, hi = min(recent), max(recent)
        blocks = "▁▂▃▄▅▆▇█"
        if hi - lo < 1e-9:
            return blocks[0] * len(recent)
        out = []
        for v in recent:
            idx = int((v - lo) / (hi - lo) * (len(blocks) - 1))
            out.append(blocks[idx])
        return "".join(out)
