"""Exchange-PAINT step builders.

For now this is a thin re-export of :meth:`ProtocolBuilder.create_steps_exchange`
plus the helper step builders that it composes. Stage-4 work will detach
the per-experiment methods from the class into module-level functions
that take a builder argument; external callers that already import from
this module then won't need to change.
"""

from PycroFlow.protocols.builder import ProtocolBuilder


def create_steps_exchange(builder: ProtocolBuilder, config):
    """Run the Exchange-PAINT step builder against ``builder``."""
    return builder.create_steps_exchange(config)
