"""Live tab host: the WP-GUI operator shell + the MM-preview toggle.

Hosts :class:`~PycroFlow.gui.live.shell.LiveShell` (kept generic — it stays a
thin seam subscriber) under a small PycroFlow-owned bar with the **Preview**
toggle: watch Micro-Manager's own Live view (or any running acquisition)
through the live pipeline with no protocol running (see
:mod:`PycroFlow.services.live_preview`). During an orchestrated run the
toggle is disabled (the run owns the camera and the shell is connected to the
run's stream instead); starting a run while previewing stops the preview.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.gui.live.shell import LiveShell


class LiveTabHost(QWidget):
    """The main window's Live tab: preview bar over the operator shell.

    Parameters
    ----------
    on_start_preview : callable or None
        Called when the operator toggles the preview ON; must return the
        preview's LiveAnalysisService (the host connects the shell to it) or
        None when preview is unavailable.
    on_stop_preview : callable or None
        Called when the operator toggles the preview OFF (or a run starts).
    on_overlay_params : callable or None
        Called with the sidebar's Core-controls payload (``{"Box Size", "Min.
        Net Gradient", ...}``) whenever the operator changes them and once at
        preview start — the preview session steers its detection-box overlay
        with these, so the drawn boxes always match the entered values.
    """

    def __init__(
        self,
        *,
        on_start_preview=None,
        on_stop_preview=None,
        on_overlay_params=None,
        parent=None,
    ):
        super().__init__(parent)
        self._on_start_preview = on_start_preview
        self._on_stop_preview = on_stop_preview
        self._on_overlay_params = on_overlay_params
        self._previewing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.preview_btn = QPushButton("Start MM preview")
        self.preview_btn.setCheckable(True)
        self.preview_btn.setToolTip(
            "Watch Micro-Manager's Live view through the live-localization "
            "pipeline (no protocol; view-only, may skip frames under load). "
            "Starts MM's Live mode if it isn't running — and stops it again "
            "when the preview ends, unless you had started it yourself."
        )
        self.preview_btn.toggled.connect(self._on_toggled)
        bar.addWidget(self.preview_btn)
        self.preview_status = QLabel("")
        bar.addWidget(self.preview_status)
        bar.addStretch()
        layout.addLayout(bar)

        self.shell = LiveShell()
        layout.addWidget(self.shell, 1)
        # Reflect a preview that ends from the SERVICE side (the sidebar
        # Early-abort stops the preview outright) on the toggle. The shell's
        # bridge already marshals seam updates onto the GUI thread; the host
        # owns the shell, so listening on its update signal is the seam-
        # consistent way to observe the stream without a second bridge.
        self.shell._bridge.update.connect(self._on_shell_update)
        # The sidebar's Core controls (box size / min net gradient) steer the
        # preview's box overlay live — this is the consumer the sidebar's
        # params_changed signal was waiting for.
        self.shell.sidebar.params_changed.connect(self._relay_overlay_params)

    # -- shell pass-through (what the main window drives) ----------------------

    def connect_service(self, service) -> None:
        """Subscribe the shell to a run's/preview's LiveAnalysisService."""
        self.shell.connect_service(service)

    def close_client(self) -> None:
        """Unsubscribe the shell (it keeps showing the last metrics)."""
        self.shell.close_client()

    # -- service-side preview end (Early-abort) ---------------------------------

    def _on_shell_update(self, update) -> None:
        """Untoggle when the PREVIEW service ends from the stream side."""
        if not self._previewing:
            return
        if update.kind == "state" and update.payload.get("state") in (
            "abort_requested",
            "experiment_ended",
        ):
            self._set_checked_silently(False)
            self._stop_preview()

    # -- preview toggle ---------------------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        if checked:
            service = (
                self._on_start_preview()
                if self._on_start_preview is not None
                else None
            )
            if service is None:
                # Unavailable (no imaging / no camera_info) — bounce back.
                self._set_checked_silently(False)
                self.preview_status.setText(
                    "preview unavailable — connect imaging / add camera_info"
                )
                return
            self._previewing = True
            self.shell.connect_service(service)
            # Seed the overlay from the sidebar so the boxes match what the
            # operator sees in the Core controls from the first frame.
            self._relay_overlay_params(self.shell.sidebar.current_params())
            self.preview_btn.setText("Stop MM preview")
            self.preview_status.setText("previewing (view-only, lossy)")
        else:
            self._stop_preview()

    def _stop_preview(self) -> None:
        if self._previewing:
            self._previewing = False
            if self._on_stop_preview is not None:
                self._on_stop_preview()
            # Unsubscribe; the shell keeps showing the last metrics.
            self.shell.close_client()
        self.preview_btn.setText("Start MM preview")
        self.preview_status.setText("")

    def _set_checked_silently(self, checked: bool) -> None:
        """Set the toggle without re-firing _on_toggled (programmatic sync)."""
        self.preview_btn.blockSignals(True)
        self.preview_btn.setChecked(checked)
        self.preview_btn.blockSignals(False)

    def _relay_overlay_params(self, params) -> None:
        if self._on_overlay_params is not None:
            self._on_overlay_params(dict(params or {}))

    # -- run coordination -------------------------------------------------------

    def set_run_lock(self, locked: bool) -> None:
        """A run owns the camera: stop any preview and disable the toggle.

        Parameters
        ----------
        locked : bool
            True while an experiment is ORCHESTRATING/RUNNING/PAUSED.
        """
        if locked and (self._previewing or self.preview_btn.isChecked()):
            self._set_checked_silently(False)
            self._stop_preview()
        self.preview_btn.setEnabled(not locked)
