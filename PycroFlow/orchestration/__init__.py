"""Orchestration framework for PycroFlow.

The original 511-line flat ``orchestration.py`` is now this package. The
``ProtocolOrchestrator`` and handler classes live in
:mod:`PycroFlow.orchestration.core`; supporting abstractions added in
Stage 4 live in :mod:`PycroFlow.orchestration.signal_registry` and
:mod:`PycroFlow.orchestration.threadexchange`.

Every public name previously importable from ``PycroFlow.orchestration``
is re-exported here so existing call sites (``frontend_cli``,
``imaging``, ``illumination``, ``hamilton_components``, the rest of the
package and external scripts) continue to work unchanged.
"""

from PycroFlow.orchestration.core import (
    AbstractSystem,
    AbstractSystemHandler,
    FluidHandler,
    ImagingHandler,
    IlluminationHandler,
    ProtocolOrchestrator,
    WaitForSignalTimeout,
    WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT,
    WAIT_POLL_INTERVAL,
)
from PycroFlow.orchestration.signal_registry import SignalRegistry
from PycroFlow.orchestration.threadexchange import ThreadExchange

__all__ = [
    "AbstractSystem",
    "AbstractSystemHandler",
    "FluidHandler",
    "ImagingHandler",
    "IlluminationHandler",
    "ProtocolOrchestrator",
    "WaitForSignalTimeout",
    "WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT",
    "WAIT_POLL_INTERVAL",
    "SignalRegistry",
    "ThreadExchange",
]
