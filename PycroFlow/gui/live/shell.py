"""The composable operator shell (C32) over WP-4's live-analysis seam.

The shell owns the chrome — a **two-row top bar**, the fixed **QC-at-a-glance
sidebar**, the always-visible **Overview/Zoom**, and the **four tab groups** — but
it does not know about specific tabs. It asks the registered
:class:`~PycroFlow.gui.live.contribution.PanelContributor`\\ s for their
:class:`PanelSpec`\\ s and mounts them under the right top-level group, so:

* registry-only install (no contributors mounting operator groups) -> just the
  chrome / a dashboard contributor's groups;
* acquisition rig (operator contributor) -> the operator tabs;
* full loop -> operator + dashboard + console contributors, side by side.

It is a **thin subscriber**: it builds a
:class:`~PycroFlow.gui.live.client.LiveClientBridge` over the service's seam and
connects that one ``update`` signal to every mounted
:class:`~PycroFlow.gui.live.contribution.StreamConsumer` and to the fixed panels.
Because subscription is per-shell, **multiple shells** over one service each get
their own bridge / trends — multi-client, straight from the seam.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from PycroFlow.gui.live.advisor import MockAdvisor
from PycroFlow.gui.live.client import LiveClientBridge
from PycroFlow.gui.live.contribution import (
    OPERATOR_GROUPS,
    ShellContext,
    collect_panels,
    iter_contributors,
    register_contributor,
)
from PycroFlow.gui.live.operator import OperatorContributor
from PycroFlow.gui.live.panels import OverviewZoom, QcAtAGlance
from PycroFlow.gui.live.theme import LIVE_QSS


def _scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setWidget(widget)
    return sa


class LiveShell(QWidget):
    """The composable operator frontend widget.

    Parameters
    ----------
    service : LiveAnalysisService or None
        The WP-4 service to subscribe to (its ``hub`` + ``request_abort``). None
        builds a passive shell (tests / a not-yet-connected UI); attach later via
        :meth:`connect_service`.
    advisor : advisor.Advisor or None
        Findings source for the sidebar light + Advisor tab. Defaults to
        :class:`~PycroFlow.gui.live.advisor.MockAdvisor`.
        TODO(WP-ADVISOR): pass ``FindingsAdapter.wrap(picasso_workflow qc_advisor)``.
    contributors : list or None
        Panel contributors to mount. None -> the registered ones (the operator
        module is auto-registered here).
    """

    def __init__(
        self,
        service=None,
        advisor=None,
        contributors=None,
        parent=None,
    ):
        super().__init__(parent)
        self._advisor = advisor if advisor is not None else MockAdvisor()
        self._bridge = LiveClientBridge(service, self)
        self._consumers: list = []
        self._run_id: Optional[str] = None

        # Contributor resolution — NO global side effect when explicit.
        #   explicit contributors= -> use exactly those, leave the global
        #     registry untouched (so two shells can show different module
        #     sets in one process, and one shell never pollutes another);
        #   contributors=None -> fall back to the process-global registry,
        #     ensuring the default operator module is present in it.
        if contributors is not None:
            self._contributors = list(contributors)
        else:
            register_contributor(OperatorContributor())
            self._contributors = iter_contributors()

        # Carry the V0.8 theme on the root so the shell looks right even when
        # mounted in a host that hasn't themed itself (app-wide Fusion + QSS is
        # still applied by the standalone/acquisition launchers via
        # apply_live_theme; this is the embedded-safe fallback).
        self.setStyleSheet(LIVE_QSS)

        self._build_ui()
        self._bridge.update.connect(self._dispatch)

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.addLayout(self._build_top_bar())

        body = QSplitter(Qt.Orientation.Horizontal)

        # Fixed sidebar (QC-at-a-glance + advisor light + core controls).
        self.sidebar = QcAtAGlance(self._advisor)
        self.sidebar.abort_requested.connect(self._bridge.request_abort)
        side_scroll = _scroll(self.sidebar)
        side_scroll.setMinimumWidth(240)
        side_scroll.setMaximumWidth(340)
        body.addWidget(side_scroll)

        # Centre: always-visible Overview/Zoom over the tab groups.
        centre = QSplitter(Qt.Orientation.Vertical)
        self.overview = OverviewZoom()
        centre.addWidget(self.overview)
        self.tab_groups = self._build_tab_groups()
        centre.addWidget(self.tab_groups)
        centre.setStretchFactor(0, 1)
        centre.setStretchFactor(1, 2)
        body.addWidget(centre)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)

        outer.addWidget(body, 1)

        # The fixed panels are stream consumers too.
        self._consumers.append(self.sidebar)
        self._consumers.append(self.overview)

    def _build_top_bar(self) -> QVBoxLayout:
        bar = QVBoxLayout()

        # Row 1: folder / status / frame · ETA.
        row1 = QHBoxLayout()
        self.folder_label = QLabel("folder: —")
        self.status_label = QLabel("state: idle")
        self.frame_label = QLabel("frame: —  ·  ETA: —")
        row1.addWidget(self.folder_label)
        row1.addStretch()
        row1.addWidget(self.status_label)
        row1.addStretch()
        row1.addWidget(self.frame_label)
        bar.addLayout(row1)

        # Row 2: run controls + export toggles + remote-monitor address.
        row2 = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.skip_btn = QPushButton("Skip")
        self.save_btn = QPushButton("Save locs")
        self.qc_btn = QPushButton("Update qc.json")
        for b in (
            self.start_btn,
            self.stop_btn,
            self.skip_btn,
            self.save_btn,
            self.qc_btn,
        ):
            row2.addWidget(b)
        row2.addStretch()
        self.monitor_label = QLabel("monitor: (in-process)")
        self.monitor_label.setToolTip(
            "Remote-monitor address (A13): the LAN web monitor re-homes behind "
            "the service; in-process today."
        )
        row2.addWidget(self.monitor_label)
        bar.addLayout(row2)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        bar.addWidget(line)
        return bar

    def _build_tab_groups(self) -> QTabWidget:
        top = QTabWidget()
        specs = collect_panels(self._contributors)
        ctx = ShellContext(client=self._bridge, advisor=self._advisor)

        # Group specs by their top-level group, preserving collect order.
        groups: dict = {}
        for spec in specs:
            if spec.group.startswith("__"):
                continue  # sidebar/reserved groups aren't tab groups
            groups.setdefault(spec.group, []).append(spec)

        # Known operator groups first (in canonical order), then any others.
        ordered_names = [g for g in OPERATOR_GROUPS if g in groups]
        ordered_names += [g for g in groups if g not in OPERATOR_GROUPS]

        for gname in ordered_names:
            gt = QTabWidget()
            for spec in groups[gname]:
                widget = spec.factory(ctx)
                gt.addTab(_scroll(widget), spec.title)
                if hasattr(widget, "on_update"):
                    self._consumers.append(widget)
            top.addTab(gt, gname)
        return top

    # -- service lifecycle -------------------------------------------------
    def connect_service(self, service) -> None:
        """Attach (or re-attach) to a WP-4 service after construction."""
        self._bridge.attach(service)

    def close_client(self) -> None:
        """Detach from the stream (multi-client: only this shell unsubscribes)."""
        self._bridge.close()

    # -- stream fan-out ----------------------------------------------------
    def _dispatch(self, update) -> None:
        # Shell-level chrome.
        if update.run_id:
            self._run_id = update.run_id
        if update.kind == "state":
            self.status_label.setText(
                "state: {}".format(update.payload.get("state", ""))
            )
        elif update.kind == "metrics":
            m = update.payload.get("metrics", {}) or {}
            self.frame_label.setText(
                "frames: {}  ·  locs: {}".format(
                    m.get("n_frames"), m.get("n_locs")
                )
            )
        # Every mounted consumer (fixed panels + contributed tabs).
        for c in self._consumers:
            try:
                c.on_update(update)
            except (
                Exception
            ):  # noqa: BLE001 - a bad panel can't stall the shell
                pass

    @property
    def consumers(self) -> List[object]:
        return list(self._consumers)


def build_live_shell(service=None, advisor=None) -> "LiveShell":
    """Convenience factory (the GUI entry point / a headless test can call it)."""
    return LiveShell(service=service, advisor=advisor)
