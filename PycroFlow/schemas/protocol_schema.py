"""Pydantic schema for PycroFlow protocol entries.

The protocol wire format is a dict of subsystems (``'fluid'``, ``'img'``,
``'illu'``); each subsystem value is a dict with a list of protocol-entry
dicts under ``'protocol_entries'``. Every entry carries a ``'$type'`` key
that selects the per-subsystem semantics.

This module pins that wire format. :func:`validate_protocol` is called by
:meth:`PycroFlow.protocols.ProtocolBuilder.create_protocol` so that a
malformed protocol is caught at construction time instead of failing
mid-run in the orchestrator's worker threads.

The validation is intentionally lenient: each entry model uses
``extra='allow'`` because existing protocols carry fields not yet enumerated
here (e.g. ``wait_time`` on inject, ``round`` on acquire). The strictness
of the discriminated union still catches typos in ``$type`` and missing
required fields.
"""

from __future__ import annotations

import sys
from typing import List, Literal, Optional, Union

# typing.Annotated landed in 3.9. Project requires 3.10+, but pydantic v2
# already depends on typing_extensions so falling back keeps the schema
# usable in any env that has pydantic.
if sys.version_info >= (3, 9):
    from typing import Annotated
else:
    from typing_extensions import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Configure each entry model so:
#   - the ``$type`` JSON key maps to the model field named ``kind`` via alias
#   - extra fields are preserved rather than rejected (back-compat)
#   - models can be constructed from Python attribute names OR JSON aliases
_ENTRY_CONFIG = ConfigDict(
    populate_by_name=True,
    extra="allow",
)


class SchemaValidationError(ValueError):
    """Raised when a protocol fails schema validation. Wraps pydantic's
    ValidationError with PycroFlow-flavored message text so callers can
    catch a single PycroFlow-specific class."""


# --- Fluid-subsystem entries ---------------------------------------------


class InjectEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["inject"] = Field(alias="$type")
    reservoir_id: int
    volume: float


class IncubateEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["incubate"] = Field(alias="$type")
    # orchestration.run_protocol coerces with float(), so str values are
    # accepted in practice (test_protocols.test_06 even asserts a string).
    duration: Union[float, str]


class FlushEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["flush"] = Field(alias="$type")
    flushfactor: float


class PumpOutEntry(BaseModel):
    """Pump-out-only step (no aspirate). ProtocolBuilder produces this for
    'remove before wash' segments of Exchange-PAINT protocols."""

    model_config = _ENTRY_CONFIG
    kind: Literal["pump_out"] = Field(alias="$type")
    volume: float


class AwaitAcquisitionEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["await_acquisition"] = Field(alias="$type")


# --- Cross-subsystem coordination entries --------------------------------


class SignalEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["signal"] = Field(alias="$type")
    value: str
    target: Optional[str] = None


class WaitForSignalEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["wait for signal"] = Field(alias="$type")
    target: str
    value: str
    timeout: Optional[float] = None


# --- Imaging-subsystem entries -------------------------------------------


class AcquireEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["acquire"] = Field(alias="$type")
    frames: int
    t_exp: float


# --- Illumination-subsystem entries --------------------------------------


class PowerEntry(BaseModel):
    """Legacy demo type — single-value power adjustment."""

    model_config = _ENTRY_CONFIG
    kind: Literal["power"] = Field(alias="$type")
    value: float


class SetPowerEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["set power"] = Field(alias="$type")
    laser: int
    power: float


class SetShutterEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["set shutter"] = Field(alias="$type")
    state: bool


class LaserEnableEntry(BaseModel):
    model_config = _ENTRY_CONFIG
    kind: Literal["laser enable"] = Field(alias="$type")
    laser: object  # int OR the string 'all'; tighten in Stage 4
    state: bool


# Discriminated union over the ``$type`` (aliased to ``kind``). pydantic
# uses this to dispatch to the right model and produces clear per-variant
# error messages.
ProtocolEntry = Annotated[
    Union[
        InjectEntry,
        IncubateEntry,
        FlushEntry,
        PumpOutEntry,
        AwaitAcquisitionEntry,
        SignalEntry,
        WaitForSignalEntry,
        AcquireEntry,
        PowerEntry,
        SetPowerEntry,
        SetShutterEntry,
        LaserEnableEntry,
    ],
    Field(discriminator="kind"),
]


class SubsystemProtocol(BaseModel):
    """One subsystem's slice of the protocol."""

    model_config = ConfigDict(extra="allow")
    protocol_entries: List[ProtocolEntry]
    parameters: Optional[dict] = None


class Protocol(BaseModel):
    """Top-level protocol model. Every subsystem is optional; absent means
    that subsystem doesn't participate in the experiment."""

    model_config = ConfigDict(extra="allow")
    fluid: Optional[SubsystemProtocol] = None
    img: Optional[SubsystemProtocol] = None
    illu: Optional[SubsystemProtocol] = None


def validate_protocol(protocol_dict):
    """Validate a protocol dict, returning the parsed :class:`Protocol`.

    Raises :class:`SchemaValidationError` on failure with a message
    describing each malformed entry. The model returned mirrors the input
    structure; callers that want to keep the raw dict (e.g. for
    serialization-stable snapshots) can use it for validation only and
    discard the returned model.
    """
    try:
        return Protocol.model_validate(protocol_dict)
    except ValidationError as exc:
        raise SchemaValidationError(
            "Protocol schema validation failed:\n{}".format(exc)
        ) from exc
