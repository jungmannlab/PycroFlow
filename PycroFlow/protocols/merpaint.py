"""MERPAINT step builders. See ``exchange.py`` for the migration plan."""
from PycroFlow.protocols.builder import ProtocolBuilder


def create_steps_MERPAINT(builder: ProtocolBuilder, config):
    return builder.create_steps_MERPAINT(config)
