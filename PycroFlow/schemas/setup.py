"""Schema + structured/passthrough split for a microscope **setup** YAML.

A setup describes one microscope's fixed hardware (pumps, reservoir
multiplexer, reservoir manifold, tubing, PFS tags, the monet config naming its
lasers). Unlike the experiment design, it was historically hand-edited YAML
with no schema; this module adds one so the GUI can offer a structured editor.

Two concerns live here:

* :class:`SetupConfig` — a pydantic model (``extra='allow'`` throughout, so no
  key is ever dropped) pinning the shape of the *structurable* fields, with the
  same ``_field`` / ``_unit`` editor metadata the schema-driven form reads.
* :func:`split_setup` / :func:`merge_setup` — the editor can only safely render
  the regular, scalar-shaped fields. The reservoir **wiring**
  (``reservoirs[].valve_pos``) carries list values and mixed int/``'ibidi'``
  keys that a scalar form would corrupt; the multiplexer has two unrelated
  shapes (Hamilton MVP vs ibidi MultiFlOW); and a setup may carry whole extra
  blocks (``monitoring``) or free-form sub-dicts (``imaging.live_analysis``).
  :func:`split_setup` carves the setup into the ``structured`` subtree the form
  edits and a ``passthrough`` subtree everything else lives in, preserved
  byte-for-byte; :func:`merge_setup` recombines them. ``merge(*split(x)) == x``
  for every setup (the round-trip guarantee the editor relies on to never lose
  or mangle a rig's wiring).
"""

from __future__ import annotations

import copy
from typing import List

from pydantic import BaseModel, ValidationError

from PycroFlow.schemas.experiment_design import _CFG, _field, _unit


class SetupValidationError(ValueError):
    """Raised when a setup dict fails :func:`validate_setup_config`."""


# --- structured sub-models (what the form edits) ------------------------------


class Interface(BaseModel):
    model_config = _CFG
    COM: str = _field(
        tooltip="Syringe-pump serial port. The Hamilton driver prefixes "
        "'COM' itself, so give the bare number (e.g. '3'), not 'COM3'."
    )
    baud: int = _field(9600, tooltip="Serial baud rate (Hamilton PSD: 9600).")


class Pump(BaseModel):
    model_config = _CFG
    address: int = _field(
        tooltip="Serial address of this pump on the Hamilton bus."
    )
    instrument_type: str = _field(tooltip="Hamilton instrument-type code.")
    valve_type: str = _field(tooltip="Pump head valve type (e.g. 'Y').")
    syringe: str = _field(
        tooltip="Syringe size code (e.g. '500u' = 500 µl, '5.0m' = 5 ml)."
    )
    input_pos: str = _field(tooltip="Valve position name used to aspirate.")
    output_pos: str = _field(tooltip="Valve position name used to dispense.")
    motorsteps_per_step: int = _field(
        1, tooltip="Motor steps per driver step."
    )
    speed_factor: int = _field(2, tooltip="Speed scaling factor.")


class Pumps(BaseModel):
    model_config = _CFG
    driver: str = _field(
        "hamilton-psd", tooltip="Syringe-pump driver backend."
    )
    interface: Interface
    pump_a: Pump = _field(tooltip="Injection syringe pump.")
    pump_out: Pump = _field(tooltip="Waste syringe pump.")


class FlushPos(BaseModel):
    model_config = _CFG
    inject: str = _field(tooltip="Pump valve position used while injecting.")
    flush: str = _field(tooltip="Pump valve position used while flushing out.")


class TubingSegment(BaseModel):
    model_config = _CFG
    from_: str = _field(
        alias="from",
        tooltip="Upstream point: a reservoir ('R7'), a valve ('V3'), "
        "'pump_a', 'sample' or 'flush_waste'.",
    )
    to: str = _field(tooltip="Downstream point (same naming as 'from').")
    volume: float = _unit(
        unit="µl", tooltip="Dead volume of tubing between the two points."
    )


class FluidStructured(BaseModel):
    """The scalar-shaped fluid fields the form edits.

    ``multiplexer`` and ``reservoirs`` are deliberately **not** declared here —
    they are passthrough (see module docstring). ``extra='allow'`` means that
    when the whole fluid block is validated they are preserved rather than
    rejected, but the form never renders them.
    """

    model_config = _CFG
    system_type: str = _field("legacy", tooltip="Fluid system backend.")
    pumps: Pumps
    flush_pos: FlushPos
    tubing: List[TubingSegment] = _field(default_factory=list)


class PfsPars(BaseModel):
    model_config = _CFG
    tag_pfs: str = _field(tooltip="MM device property tag for the PFS offset.")
    tag_zdrive: str = _field(tooltip="MM device property tag for the z-drive.")
    tag_status: str = _field(tooltip="MM device tag for the PFS status.")
    prop_state: str = _field(tooltip="MM property name for the PFS state.")
    prop_status: str = _field(tooltip="MM property name for the PFS status.")
    deltat: int = _unit(
        unit="ms", tooltip="PFS polling interval while monitoring focus."
    )


class ImagingStructured(BaseModel):
    """Structurable imaging fields.

    Only the always-present PFS tags are structured. ``camera_info`` and
    ``live_analysis`` stay passthrough: ``camera_info`` is *optional*, and the
    schema-driven form materializes an optional nested model with zero
    defaults even when the setup has none — which would inject a bogus
    zero-calibration block and silently enable live localization. Editing it in
    the preserved raw panel keeps an absent block absent.
    """

    model_config = _CFG
    pfs_pars: PfsPars


class Illumination(BaseModel):
    model_config = _CFG
    backend: str = _field("monet", tooltip="Illumination control backend.")
    config: str = _field(
        tooltip="The monet CONFIGS key naming this microscope's lasers "
        "(drives the Monet tab and the design's laser dropdown)."
    )


class SetupConfig(BaseModel):
    """A microscope setup. ``extra='allow'`` keeps any unmodeled key."""

    model_config = _CFG
    setup: str = _field(tooltip="Setup name (also the monet.CONFIGS key).")
    emulated: bool = _field(
        False,
        tooltip="Build the real drivers over fake serial wires (no hardware).",
    )
    fluid: FluidStructured
    imaging: ImagingStructured
    illumination: Illumination


def validate_setup_config(data) -> SetupConfig:
    """Validate a setup dict, returning the parsed model.

    Parameters
    ----------
    data : dict
        The setup (e.g. parsed from YAML).

    Returns
    -------
    SetupConfig
        The validated model. ``extra='allow'`` means passthrough blocks
        (multiplexer, reservoirs, monitoring, ...) are retained, not rejected.

    Raises
    ------
    SetupValidationError
        If validation fails.
    """
    try:
        return SetupConfig.model_validate(data)
    except ValidationError as exc:
        raise SetupValidationError(str(exc)) from exc


# --- structured / passthrough split (what the editor splits on) ---------------

#: The fields the structured form owns, as dotted paths into the setup dict.
#: Everything NOT listed here is passthrough (preserved verbatim, edited as raw
#: YAML). ``reservoirs``/``multiplexer`` are the wiring the form can't render
#: safely; ``imaging.live_analysis`` and any top-level extras come along too.
STRUCTURED_PATHS = (
    ("setup",),
    ("emulated",),
    ("fluid", "system_type"),
    ("fluid", "pumps"),
    ("fluid", "flush_pos"),
    ("fluid", "tubing"),
    ("imaging", "pfs_pars"),
    ("illumination",),
)


def _get_path(data: dict, path: tuple) -> tuple:
    """Return ``(present, value)`` for the dotted ``path`` into ``data``."""
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return (False, None)
        node = node[part]
    return (True, node)


def _set_path(data: dict, path: tuple, value) -> None:
    """Set ``data[path...] = value``, creating intermediate dicts as needed."""
    node = data
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


def split_setup(data: dict) -> tuple[dict, dict]:
    """Split a setup dict into ``(structured, passthrough)``.

    ``structured`` holds exactly the :data:`STRUCTURED_PATHS` the form edits;
    ``passthrough`` holds everything else (reservoir wiring, the multiplexer,
    ``monitoring``, free-form sub-dicts, any unknown key), preserved verbatim.
    The two are disjoint, and :func:`merge_setup` recombines them exactly:
    ``merge_setup(*split_setup(x)) == x``.
    """
    data = copy.deepcopy(data)
    structured: dict = {}
    # Parent dicts we removed a structured child from — only these may be
    # pruned if they end up empty, so a block that was ALREADY empty on disk is
    # preserved (keeping the round-trip exact even for a degenerate setup).
    touched_parents = set()
    for path in STRUCTURED_PATHS:
        present, value = _get_path(data, path)
        if present:
            _set_path(structured, path, value)
            _del_path(data, path)
            if len(path) > 1:
                touched_parents.add(path[:-1])
    for parent in sorted(touched_parents, key=len, reverse=True):
        present, node = _get_path(data, parent)
        if present and node == {}:
            _del_path(data, parent)
    return structured, data


def _del_path(data: dict, path: tuple) -> None:
    """Delete ``data[path...]`` if present (no-op on a missing intermediate)."""
    node = data
    for part in path[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            return
        node = nxt
    node.pop(path[-1], None)


def merge_setup(structured: dict, passthrough: dict) -> dict:
    """Recombine a structured + passthrough split back into one setup dict.

    Both inputs are deep-copied, so the result shares no nested objects with
    either argument (a later mutation of the result can't reach back into the
    caller's ``structured``/``passthrough`` dicts).
    """
    return _deep_merge(copy.deepcopy(passthrough), copy.deepcopy(structured))


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge ``overlay`` into ``base`` (overlay wins), returning
    ``base``. Nested dicts merge; everything else is replaced."""
    for key, value in overlay.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
