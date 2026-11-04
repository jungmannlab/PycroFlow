"""A global replay transport bar shown ABOVE the tab widget.

Owns the :class:`~PycroFlow.live_analysis.replay_player.ReplayController` and the
timeline slider / play / speed controls, so a finished (or opened-from-disk) run
can be scrubbed while viewing ANY tab — the controller's sinks drive the live
view, webcam, fluid schematic and the Run Sequence step-highlight regardless of
which tab is in front. The main window builds the controller (wiring its sinks
into the tabs) and hands it here via :meth:`set_controller`.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QComboBox,
    QLabel,
    QFileDialog,
)


class ReplayBar(QWidget):
    """Replay transport: Open-past-run + Replay toggle + slider/play/speed.

    Callbacks the main window sets:

    * ``on_mode(bool)`` — entering/leaving replay (coordinate the tabs: pause the
      fluid/webcam live polls, flag the live tab).
    * ``on_open_past_run(path)`` — a saved ``*_replay.jsonl`` was chosen.
    """

    _SPEEDS = (0.5, 1.0, 2.0, 4.0, 8.0)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller = None
        self._duration = 0
        self._pos = 0
        self._speed = 1.0
        self._mode = False
        self.on_mode = None
        self.on_open_past_run = None
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    def _build_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.addWidget(QLabel("Replay:"))
        self.open_btn = QPushButton("Open past run…")
        row.addWidget(self.open_btn)
        self.replay_btn = QPushButton("▶ Replay run")
        self.replay_btn.setCheckable(True)
        self.replay_btn.setEnabled(False)
        row.addWidget(self.replay_btn)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(32)
        row.addWidget(self.play_btn)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        row.addWidget(self.slider, 1)
        self.time_label = QLabel("0:00 / 0:00")
        row.addWidget(self.time_label)
        self.speed_label = QLabel("speed")
        row.addWidget(self.speed_label)
        self.speed_combo = QComboBox()
        for s in ("0.5×", "1×", "2×", "4×", "8×"):
            self.speed_combo.addItem(s)
        self.speed_combo.setCurrentIndex(1)
        row.addWidget(self.speed_combo)

        self.open_btn.clicked.connect(self._on_open)
        self.replay_btn.toggled.connect(self._on_toggled)
        self.play_btn.clicked.connect(self._on_play)
        self.slider.valueChanged.connect(self._on_slider)
        self.speed_combo.currentIndexChanged.connect(self._on_speed)
        self._set_transport_visible(False)

    def _set_transport_visible(self, on):
        """Show the slider/play/speed only while replaying (a thin two-button
        bar otherwise)."""
        for w in (
            self.play_btn,
            self.slider,
            self.time_label,
            self.speed_label,
            self.speed_combo,
        ):
            w.setVisible(on)

    # --- public API ----------------------------------------------------

    def set_controller(self, controller, duration_ms):
        """Attach a replay timeline (or clear it with ``None``, 0).

        Enables the Replay toggle; clearing exits replay mode. The controller's
        sinks (live view / webcam / fluid schematic / step-highlight) are wired
        by the main window before this is called.
        """
        self._controller = controller
        self._duration = int(duration_ms or 0)
        available = controller is not None and self._duration > 0
        self.replay_btn.setEnabled(available)
        if not available and self.replay_btn.isChecked():
            self.replay_btn.setChecked(False)  # exits replay (toggled)

    def enter_replay(self):
        """Programmatically open replay mode (used when opening a past run)."""
        self.replay_btn.setChecked(True)

    def seek_to_step(self, system, index):
        """Seek to the moment ``system`` reached step ``index`` (protocol jump).

        No-op when that step was never reached, so the position holds.
        """
        if self._controller is None:
            return
        t = self._controller.first_time_reaching(system, index)
        if t is not None:
            self.slider.setValue(int(t))  # -> _on_slider -> seek

    @property
    def active(self) -> bool:
        return self._mode

    # --- handlers ------------------------------------------------------

    def _on_open(self):
        if not callable(self.on_open_past_run):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open a past run to replay",
            "",
            "Replay timelines (*_replay.jsonl);;All files (*)",
        )
        if path:
            self.on_open_past_run(path)

    def _on_toggled(self, checked):
        self._mode = checked
        self._set_transport_visible(checked)
        if callable(self.on_mode):
            self.on_mode(checked)
        if checked:
            self._speed = self._SPEEDS[self.speed_combo.currentIndex()]
            self.slider.setRange(0, self._duration)
            self._pos = 0
            self.slider.setValue(0)
            self._seek(0)
        else:
            self._timer.stop()
            self.play_btn.setText("▶")

    def _on_play(self):
        if not self._mode:
            return
        if self._timer.isActive():
            self._timer.stop()
            self.play_btn.setText("▶")
        else:
            if self._pos >= self._duration:
                self._pos = 0  # restart if parked at the end
                self.slider.setValue(0)
            self._timer.start()
            self.play_btn.setText("⏸")

    def _tick(self):
        step = int(self._timer.interval() * self._speed)
        self._pos = min(self._duration, self._pos + step)
        self.slider.setValue(self._pos)  # -> _on_slider -> seek
        if self._pos >= self._duration:
            self._timer.stop()
            self.play_btn.setText("▶")

    def _on_slider(self, value):
        self._pos = value
        self._seek(value)

    def _on_speed(self, index):
        if 0 <= index < len(self._SPEEDS):
            self._speed = self._SPEEDS[index]

    def _seek(self, t_ms):
        if self._controller is not None:
            self._controller.seek(t_ms)
        self.time_label.setText(
            "{} / {}".format(self._fmt(t_ms), self._fmt(self._duration))
        )

    @staticmethod
    def _fmt(ms):
        s = int(ms // 1000)
        return "{}:{:02d}".format(s // 60, s % 60)
