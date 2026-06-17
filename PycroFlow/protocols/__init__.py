"""Protocol builder + per-experiment-type modules.

``ProtocolBuilder`` lived in a single 1.1k-line ``protocols.py`` until
Stage 3. The class moved to :mod:`PycroFlow.protocols.builder`; the
per-experiment step-builder methods (``create_steps_exchange``,
``create_steps_MERPAINT``, ``create_steps_flushtest``,
``create_steps_sph_resi``) remain attached to the class so existing call
chains work unchanged.

This package's submodules (``exchange``, ``merpaint``, ``flushtest``,
``sph_resi``) currently just re-export the matching ``ProtocolBuilder``
method so external code can write::

    from PycroFlow.protocols.exchange import create_steps_exchange

A future Stage-4 cleanup may detach the methods into free functions and
register them via :attr:`ProtocolBuilder.EXPERIMENT_TYPES`.

Back-compat re-exports keep ``from PycroFlow.protocols import
ProtocolBuilder`` working.
"""
from PycroFlow.protocols.builder import ProtocolBuilder

__all__ = ["ProtocolBuilder"]
