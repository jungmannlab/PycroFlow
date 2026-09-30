"""Wire-format schemas for PycroFlow.

Pydantic models that pin the shape of protocol dicts produced by
:class:`PycroFlow.protocols.ProtocolBuilder` and consumed by the
orchestrator. The wire format itself is unchanged — these models only
catch malformed inputs (missing required fields, unknown ``$type``s,
typos in field names) before the orchestrator starts and finds out the
hard way mid-run.

Stage 4 of the restructuring will turn these from validation-only into
the canonical typed representation of protocol entries.
"""

from PycroFlow.schemas.protocol_schema import (
    Protocol,
    ProtocolEntry,
    SubsystemProtocol,
    SchemaValidationError,
    validate_protocol,
)
from PycroFlow.schemas.experiment_design import (
    ExperimentDesign,
    ExperimentDesignValidationError,
    validate_experiment_design,
)

__all__ = [
    "Protocol",
    "ProtocolEntry",
    "SubsystemProtocol",
    "SchemaValidationError",
    "validate_protocol",
    "ExperimentDesign",
    "ExperimentDesignValidationError",
    "validate_experiment_design",
]
