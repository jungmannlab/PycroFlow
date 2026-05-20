"""Frontend-agnostic service layer.

Both :mod:`PycroFlow.frontend_cli` and the upcoming Qt GUI (Stage 5) talk
to PycroFlow through this layer instead of poking ``ProtocolOrchestrator``
internals directly. That keeps the orchestrator focused on threading /
signaling, and lets the GUI subscribe to state-change events via an
observer hook without needing Qt-specific code in the orchestrator.

* :func:`mm_core.get_core` — single owner of the Micro-Manager Core
  connection. Supersedes ``util.PyMgrSingleton``. In Stage 5 the same
  function is patched into monet so the two packages share one Core
  inside the GUI process.
* :class:`ExperimentService` — protocol load / start / pause / resume /
  abort, plus observer hooks for state-change and log events.
* :class:`SystemService` — hardware-control commands (manual pump moves,
  tubing fill, system cleanup) that the CLI exposes.
"""
from PycroFlow.services.mm_core import get_core, get_studio, reset_core
from PycroFlow.services.experiment_service import (
    ExperimentService,
    ExperimentState,
)
from PycroFlow.services.system_service import SystemService

__all__ = [
    "get_core",
    "get_studio",
    "reset_core",
    "ExperimentService",
    "ExperimentState",
    "SystemService",
]
