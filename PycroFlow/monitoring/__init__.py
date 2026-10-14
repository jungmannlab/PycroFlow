"""Fluidics monitoring webcams (WP-FLUIDICS-CAM, Phase 1).

A read-only, best-effort subsystem that records one short movie per
Exchange round of the least-observable leg of an automated PAINT run --
the fluid exchange (dry reservoir, mis-primed pump, bubble/air-gap, leak,
kinked tubing). The clips make a run reviewable after the fact.

Design invariants (the load-bearing ones the WP gate checks):

* **Separate process.** The camera capture runs in its own OS process
  (:mod:`PycroFlow.monitoring.capture_service`), spawned and torn down by
  :class:`PycroFlow.monitoring.controller.MonitoringController`. Nothing
  the cameras do can block or perturb acquisition or the fluidics
  orchestration -- the WP-1 lesson.
* **Best-effort everywhere.** A slow/saturated bus, a dropped frame, a
  downed/unplugged camera, or a down/slow registry degrades to a logged
  "gap / no clip" and never raises into, stalls, or slows the run.
* **Optional.** No ``monitoring:`` block / no cameras in the setup => the
  subsystem is inert and a no-fluidics / no-camera rig runs unchanged.
* **Read-only.** No actuation; outside the safety envelope.

The camera library (OpenCV) is the optional ``[monitoring]`` extra and is
needed only for the *instrument* frame source; the synthetic ``--emulator``
source and the pure-Python AVI writer are wheel-only so the emulator tests
run in headless CI with no camera and no extra deps.
"""

from __future__ import annotations

from PycroFlow.monitoring.config import (
    CameraConfig,
    MonitoringConfig,
    load_monitoring_config,
)
from PycroFlow.monitoring.controller import (
    MonitoringController,
    attach_monitoring,
)

__all__ = [
    "CameraConfig",
    "MonitoringConfig",
    "load_monitoring_config",
    "MonitoringController",
    "attach_monitoring",
]
