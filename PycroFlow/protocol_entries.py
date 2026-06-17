"""Typed protocol entries — the Stage-4 canonical API.

The pydantic models defining the wire format live in
:mod:`PycroFlow.schemas.protocol_schema` (added in Stage 2). This module
re-exports them at the top of the package, matching the structure the
restructuring plan calls for, and adds :func:`parse_protocol` — a thin
wrapper around the schema's validator that returns the typed
``Protocol`` model.

Stage-4 dispatchers (added in :mod:`PycroFlow.orchestration.core`) call
:func:`parse_entry` to coerce a raw dict into the appropriate typed
entry, then dispatch on the typed class via ``functools.singledispatch``.
Callers that still pass raw dicts work because the orchestration code
parses lazily and falls back to the original ``$type`` string dispatch.
"""
from PycroFlow.schemas import validate_protocol
from PycroFlow.schemas.protocol_schema import (
    AcquireEntry,
    AwaitAcquisitionEntry,
    FlushEntry,
    IncubateEntry,
    InjectEntry,
    LaserEnableEntry,
    PowerEntry,
    Protocol,
    ProtocolEntry,
    PumpOutEntry,
    SchemaValidationError,
    SetPowerEntry,
    SetShutterEntry,
    SignalEntry,
    SubsystemProtocol,
    WaitForSignalEntry,
)


# Mapping ``$type`` value -> entry model. Used by :func:`parse_entry` so a
# raw dict can be coerced without going through the full Protocol model
# (which requires a fully-formed protocol dict).
ENTRY_MODELS_BY_TYPE = {
    'inject': InjectEntry,
    'incubate': IncubateEntry,
    'flush': FlushEntry,
    'pump_out': PumpOutEntry,
    'await_acquisition': AwaitAcquisitionEntry,
    'signal': SignalEntry,
    'wait for signal': WaitForSignalEntry,
    'acquire': AcquireEntry,
    'power': PowerEntry,
    'set power': SetPowerEntry,
    'set shutter': SetShutterEntry,
    'laser enable': LaserEnableEntry,
}


def parse_protocol(raw):
    """Validate a raw protocol dict and return the typed :class:`Protocol`.

    Equivalent to :func:`PycroFlow.schemas.validate_protocol` — re-exposed
    here so callers that want typed entries do not have to know about the
    schemas/ subpackage layout. Raises
    :class:`PycroFlow.schemas.SchemaValidationError` on failure.
    """
    return validate_protocol(raw)


def parse_entry(raw):
    """Coerce a raw entry dict into the matching typed model.

    Looks up ``raw['$type']`` (case-insensitively, matching the existing
    orchestrator's ``step['$type'].lower()`` dispatch) in
    :data:`ENTRY_MODELS_BY_TYPE` and validates via the corresponding
    pydantic model. Used by the Stage-4 dispatcher so individual entries
    can be promoted without parsing the whole protocol.

    Raises :class:`KeyError` for unknown ``$type``s and the underlying
    ``pydantic.ValidationError`` for malformed fields.
    """
    type_key = raw['$type']
    model = ENTRY_MODELS_BY_TYPE.get(type_key)
    if model is None and isinstance(type_key, str):
        model = ENTRY_MODELS_BY_TYPE.get(type_key.lower())
    if model is None:
        raise KeyError(
            "unknown protocol entry $type {!r}; known types: {}".format(
                type_key, sorted(ENTRY_MODELS_BY_TYPE),
            )
        )
    # Lowercase the dispatch field in the input so the Literal discriminator
    # matches. The pydantic model preserves all other fields verbatim.
    if isinstance(type_key, str) and type_key not in ENTRY_MODELS_BY_TYPE:
        raw = dict(raw, **{'$type': type_key.lower()})
    return model.model_validate(raw)


__all__ = [
    "Protocol",
    "ProtocolEntry",
    "SubsystemProtocol",
    "SchemaValidationError",
    "ENTRY_MODELS_BY_TYPE",
    "parse_protocol",
    "parse_entry",
    # Per-type entry models
    "InjectEntry",
    "IncubateEntry",
    "FlushEntry",
    "PumpOutEntry",
    "AwaitAcquisitionEntry",
    "SignalEntry",
    "WaitForSignalEntry",
    "AcquireEntry",
    "PowerEntry",
    "SetPowerEntry",
    "SetShutterEntry",
    "LaserEnableEntry",
]
