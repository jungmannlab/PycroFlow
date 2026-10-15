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


#: Subsystems that may appear in a protocol, in display order.
_SYSTEMS = ("fluid", "img", "illu")


def check_runnable(protocol) -> list:
    """Check a protocol can run to completion without hanging.

    The three subsystems run as independent entry lists that synchronise only
    through ``signal`` / ``wait for signal`` rendezvous. A hand-edited (or
    mis-built) sequence can therefore deadlock two ways, both of which this
    catches:

    1. **Orphan wait** — a ``wait for signal`` whose ``value`` is never emitted
       by a ``signal`` in its ``target`` subsystem. That wait blocks forever.
    2. **Circular wait** — a cycle in the happens-before graph (program order
       within each subsystem, plus every ``signal`` → the ``wait`` that
       consumes it). A cycle means no execution order satisfies all the waits,
       so the run hangs.

    This is a *runnability* check, distinct from :func:`validate_protocol`
    (which pins the wire format). It is advisory for the GUI's post-edit hint
    and a hard gate at run start.

    Parameters
    ----------
    protocol : dict
        A compiled protocol (``{system: {'protocol_entries': [...]}}``), or a
        :class:`Protocol`-shaped mapping.

    Returns
    -------
    list of str
        Human-readable problem descriptions; empty when the sequence is sound.
    """
    protocol = protocol or {}
    # Gather entries per subsystem (tolerant of absent / malformed slices).
    entries = {}
    for system in _SYSTEMS:
        sub = protocol.get(system) if isinstance(protocol, dict) else None
        if isinstance(sub, dict):
            raw = sub.get("protocol_entries") or []
            entries[system] = [e for e in raw if isinstance(e, dict)]
        else:
            entries[system] = []

    # (system, value) pairs actually emitted by a ``signal`` step.
    emitted = set()
    for system, es in entries.items():
        for e in es:
            if e.get("$type") == "signal" and e.get("value") is not None:
                emitted.add((system, e.get("value")))

    def _satisfies(target, value):
        """Whether ``target`` emits ``value`` under the orchestrator's rules.

        Mirrors AbstractSystemHandler: a wait for ``value`` from ``target`` is
        satisfied by ``target`` emitting exactly ``value`` OR the legacy
        prefixed form ``"<target> <value>"`` (core.py send_message/wait).
        """
        if (target, value) in emitted:
            return True
        return (target, "{} {}".format(target, value)) in emitted

    issues = []

    # 1) Orphan waits: the target subsystem must emit the awaited value.
    for system, es in entries.items():
        for i, e in enumerate(es):
            if e.get("$type") != "wait for signal":
                continue
            value = e.get("value")
            target = e.get("target")
            if target is None or not _satisfies(target, value):
                issues.append(
                    "{} step {}: waits for signal {!r} from {!r} that is "
                    "never emitted — the run would hang here.".format(
                        system, i, value, target
                    )
                )

    # 2) Circular waits: topologically sort the happens-before graph. Nodes are
    # (system, index); edges are program order + signal -> consuming wait. A
    # leftover node after Kahn's algorithm sits on a cycle = a deadlock.
    nodes = [(s, i) for s in _SYSTEMS for i in range(len(entries[s]))]
    adj = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}

    def _add_edge(src, dst):
        adj[src].append(dst)
        indeg[dst] += 1

    for system in _SYSTEMS:
        for i in range(1, len(entries[system])):
            _add_edge((system, i - 1), (system, i))
    # signal -> wait edges (only where the awaited value is actually emitted).
    # Key by (emitting_system, value) so a wait links to the signals its
    # ``target`` emits (plus the legacy "<target> <value>" prefixed form).
    sig_nodes = {}
    for system in _SYSTEMS:
        for i, e in enumerate(entries[system]):
            if e.get("$type") == "signal" and e.get("value") is not None:
                sig_nodes.setdefault((system, e.get("value")), []).append(
                    (system, i)
                )
    for system in _SYSTEMS:
        for i, e in enumerate(entries[system]):
            if e.get("$type") != "wait for signal":
                continue
            target = e.get("target")
            value = e.get("value")
            srcs = sig_nodes.get((target, value), []) + sig_nodes.get(
                (target, "{} {}".format(target, value)), []
            )
            for src in srcs:
                _add_edge(src, (system, i))

    ready = [n for n in nodes if indeg[n] == 0]
    seen = 0
    while ready:
        n = ready.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if seen < len(nodes):
        stuck = sorted(n for n in nodes if indeg[n] > 0)
        waits = [
            "{} step {}".format(s, i)
            for (s, i) in stuck
            if entries[s][i].get("$type") == "wait for signal"
        ]
        where = ", ".join(waits[:6]) if waits else "the coordination steps"
        issues.append(
            "Circular wait detected: {} form a dependency cycle, so no "
            "execution order satisfies all the waits — the run would "
            "deadlock.".format(where)
        )

    return issues


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
