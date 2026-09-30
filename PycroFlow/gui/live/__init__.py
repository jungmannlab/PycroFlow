"""WP-GUI: the composable operator frontend over WP-4's live-analysis seam.

This subpackage is the V0.8-modelled operator UI, rebuilt as a **composable
shell** (C32) over WP-4's streaming :mod:`PycroFlow.live_analysis.client_seam`
(A13/C23 thin client). Nothing here is imported at ``PycroFlow.gui`` package
import time — every module imports PyQt6 itself and is lazy, so the package stays
headless-importable (CI runs with ``QT_QPA_PLATFORM=offscreen``).

Structure:

* :mod:`PycroFlow.gui.live.client` — the thin subscribing client that turns
  seam :class:`~PycroFlow.live_analysis.client_seam.LiveUpdate`\\ s into Qt
  signals and holds the (in-process) control call. The single transport-swap
  point: a WebSocket/SSE+REST client later implements the same surface.
* :mod:`PycroFlow.gui.live.contribution` — the C32 contribution contract: a
  shell mounts panels/tab-groups contributed by whatever modules are active.
* :mod:`PycroFlow.gui.live.advisor` — the local advisor-findings Protocol +
  adapter (+ mock), so we render advisor output without hard-depending on
  picasso-workflow's ``qc_advisor`` (built in parallel, WP-ADVISOR).
* :mod:`PycroFlow.gui.live.panels` — the fixed sidebar QC-at-a-glance + the
  always-visible Overview/Zoom panel.
* :mod:`PycroFlow.gui.live.operator` — the first contributor: the operator
  module's four tab groups (Setup / Live QC / Analysis / Assistant).
* :mod:`PycroFlow.gui.live.shell` — the composable shell that assembles the top
  bar + sidebar + mounted contributions and subscribes them to the client.
"""
