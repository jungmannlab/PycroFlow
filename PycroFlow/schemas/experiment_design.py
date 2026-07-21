"""Pydantic schema for the high-level **Experiment Design**.

The Experiment Design is the human-authored description of an experiment
(volumes, velocities, reservoir names, the per-experiment-type design such as
the SPH-RESI target / RESI rounds).
:class:`PycroFlow.protocols.ProtocolBuilder`
compiles it into the linearized **Run Sequence** (pinned by
:mod:`PycroFlow.schemas.protocol_schema`).

This module models the two experiment types the GUI edits structurally —
``Exchange`` and ``SPH-RESI`` — taken from the fields
:class:`~PycroFlow.protocols.builder.ProtocolBuilder` actually consumes. As in
``protocol_schema``, every model uses ``extra='allow'`` so designs carrying
not-yet-modeled fields still load, and hyphenated keys (``target-rounds``,
``RESI-imager``, ...) are mapped via ``Field(alias=...)`` with
``populate_by_name=True``.

The schema is the single source of truth both for builder/GUI validation and
for the schema-driven structured editor (which renders from ``model_fields``).
"""

from __future__ import annotations

import sys
from typing import Dict, List, Literal, Optional, Union

if sys.version_info >= (3, 9):
    from typing import Annotated
else:  # pragma: no cover - project requires 3.10+
    from typing_extensions import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

# populate_by_name: accept both python name and JSON alias.
# extra='allow': keep fields we have not modeled yet (forward-compat).
_CFG = ConfigDict(populate_by_name=True, extra="allow")


def _field(default=..., *, alias=None, default_factory=None, **extra):
    """A ``Field`` carrying editor metadata in ``json_schema_extra``.

    All metadata is advisory (it does not affect validation); the
    schema-driven GUI editor reads it to render richer inputs. Recognised
    keys: ``unit`` (label), ``choices`` (static dropdown options),
    ``choices_from`` (dropdown options from an editor *context* key),
    ``allow_none`` (add a blank → None option), ``allow_custom`` (the
    dropdown is editable, so a value outside the option set — or any value
    when the set is unknown — can be typed), ``tooltip``, and for mappings
    ``columns`` / ``display_value_first`` / ``key_choices_from`` /
    ``value_choices_from``. ``default=...`` (the pydantic sentinel) marks a
    required field.

    Units used here: ``µl`` (volumes), ``µl/min`` (velocities), ``s``
    (delays), ``min`` (incubations), ``ms`` (exposure), ``mW`` (laser power).
    """
    extra = {k: v for k, v in extra.items() if v is not None}
    kw = {"alias": alias, "json_schema_extra": (extra or None)}
    if default_factory is not None:
        return Field(default_factory=default_factory, **kw)
    return Field(default, **kw)


def _unit(default=..., unit=None, *, alias=None):
    """Shorthand for a numeric field with only a physical ``unit``."""
    return _field(default, alias=alias, unit=unit)


def field_meta(field_info) -> dict:
    """Return a model field's editor metadata dict (empty if none).

    Parameters
    ----------
    field_info : pydantic.fields.FieldInfo
        An entry of ``model.model_fields``.
    """
    extra = getattr(field_info, "json_schema_extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def field_unit(field_info) -> Optional[str]:
    """Return a model field's declared unit, or ``None``."""
    return field_meta(field_info).get("unit")


class ExperimentDesignValidationError(ValueError):
    """Raised when an experiment design fails schema validation.

    Wraps pydantic's ``ValidationError`` so callers catch a single
    PycroFlow-specific class, mirroring
    :class:`PycroFlow.schemas.protocol_schema.SchemaValidationError`.
    """


# --- SPH-RESI nested blocks ----------------------------------------------


class ResiRound(BaseModel):
    """One RESI round: which adapter, and how long to incubate it."""

    model_config = _CFG
    adapter: str = _field(choices_from="reservoir_names", allow_none=True)
    adapter_incubation: float = _unit(unit="min")


class TargetRound(BaseModel):
    """Per-target parameters for an SPH-RESI run."""

    model_config = _CFG
    bc_imager_pre: str = _field(
        alias="BC_imager_pre", choices_from="reservoir_names", allow_none=True
    )
    frames_bc_pre: int = Field(alias="frames_BC_pre")
    bc_imager_post: str = _field(
        alias="BC_imager_post", choices_from="reservoir_names", allow_none=True
    )
    frames_bc_post: int = Field(alias="frames_BC_post")
    resi_imager: str = _field(
        alias="RESI-imager", choices_from="reservoir_names", allow_none=True
    )
    resi_frames: int = Field(alias="RESI-frames")
    resi_rounds: List[ResiRound] = Field(alias="RESI-rounds")


class Round0(BaseModel):
    """Optional pre-target imaging round (e.g. alignment structures)."""

    model_config = _CFG
    round0_imager: str = _field(
        choices_from="reservoir_names", allow_none=True
    )
    frames_round0: int


# --- experiment-type design blocks (discriminated on ``type``) -----------


class ExchangeExperiment(BaseModel):
    """Exchange-PAINT experiment design."""

    model_config = _CFG
    type: Literal["Exchange"]
    wash_buffer: str = _field(choices_from="reservoir_names", allow_none=True)
    initial_imager: Optional[str] = _field(
        None, choices_from="reservoir_names", allow_none=True
    )
    # One dropdown row per exchange round (add/remove), chosen from the
    # design's reservoir names; shown as a 'rounds' box with per-row labels.
    imagers: List[str] = _field(
        default_factory=list,
        choices_from="reservoir_names",
        allow_none=True,
        title="rounds",
        row_label="imager round {}",
    )


class SphResiExperiment(BaseModel):
    """SPH-RESI experiment design."""

    model_config = _CFG
    type: Literal["SPH-RESI"]
    wash_buffer_1: str = _field(
        choices_from="reservoir_names", allow_none=True
    )
    wash_buffer_2: Optional[str] = _field(
        None, choices_from="reservoir_names", allow_none=True
    )
    blocker: str = _field(choices_from="reservoir_names", allow_none=True)
    blocker_incubation: float = _unit(unit="min")
    initial_imager_present: bool = False
    round0: Optional[Round0]
    target_rounds: Dict[str, TargetRound] = Field(alias="target-rounds")


ExperimentBlock = Annotated[
    Union[ExchangeExperiment, SphResiExperiment],
    Field(discriminator="type"),
]


# --- fluid / img / illu sections -----------------------------------------


class FluidParameters(BaseModel):
    """Per-run fluid driver parameters (passed through to the Run Sequence)."""

    model_config = _CFG
    start_velocity: float = _unit(500, "µl/min")
    max_velocity: float = _unit(10000, "µl/min")
    stop_velocity: float = _unit(500, "µl/min")
    pumpout_dispense_velocity: float = _unit(290000, "µl/min")
    clean_velocity: float = _unit(10000, "µl/min")
    clean_delay: float = _unit(0, "s")
    mode: str = _field(
        "tubing_ignore", choices=["tubing_ignore", "tubing_stack"]
    )
    extractionfactor: float = 1
    inject_pickup_extravol: float = _unit(0, "µl")
    inject_in_to_out_delay: float = _unit(0, "s")
    inject_out_to_in_delay: float = _unit(0, "s")
    inject_precreate_underpressure: bool = False


class FluidSettings(BaseModel):
    """Experiment-level fluid settings + the experiment-type design block.

    The reservoir tables come first (they define the names the rest of the
    design refers to). Wash buffers are not repeated here — they live in the
    ``experiment`` block.
    """

    model_config = _CFG
    reservoir_names: Dict[int, str] = _field(
        key_choices_from="reservoir_ids",
        columns=["Reservoir ID", "Name"],
        # The names defined here populate the imager/buffer dropdowns; publish
        # them live so those dropdowns update as the table is edited.
        provides="reservoir_names",
    )
    # Stored name -> id, but displayed (ID, name) for consistency with
    # reservoir_names (display_value_first swaps the columns; the id column
    # offers the setup's reservoir ids).
    special_names: Dict[str, int] = _field(
        default_factory=dict,
        display_value_first=True,
        value_choices_from="reservoir_ids",
        columns=["Reservoir ID", "Special name"],
    )
    vol_wash: float = _unit(unit="µl")
    vol_reagent: Optional[float] = _unit(None, "µl")
    vol_imager_post: Optional[float] = _unit(None, "µl")
    vol_remove_before_flush: float = _unit(0, "µl")
    wait_after_pickup: float = _unit(0, "s")
    cleaning_reservoirs: List[Union[int, str]] = _field(
        default_factory=list,
        tooltip="Comma-separated reservoir ids or special names used for "
        'cleaning, e.g. "h2o, ipa".',
    )
    experiment: ExperimentBlock


class FluidSection(BaseModel):
    model_config = _CFG
    enabled: bool = _field(
        True,
        tooltip="Include this subsystem when translating to the Run "
        "Sequence. Deselect to leave it out of the run.",
    )
    parameters: FluidParameters = Field(default_factory=FluidParameters)
    settings: FluidSettings


class ImgParameters(BaseModel):
    model_config = _CFG
    show_progress: bool = True
    show_display: bool = True
    close_display_after_acquisition: bool = True


class ImgSettings(BaseModel):
    model_config = _CFG
    t_exp: float = _unit(unit="ms")
    # frames may be a single count or a per-imager mapping (Exchange).
    frames: Optional[Union[int, Dict[str, int]]] = None
    darkframes: Optional[int] = None


class ImgSection(BaseModel):
    model_config = _CFG
    enabled: bool = _field(
        True,
        tooltip="Include this subsystem when translating to the Run "
        "Sequence. Deselect to leave it out of the run.",
    )
    parameters: ImgParameters = Field(default_factory=ImgParameters)
    settings: ImgSettings


class IlluSettings(BaseModel):
    model_config = _CFG
    laser: int = _field(
        choices_from='lasers', allow_custom=True,
        tooltip=("Laser line (nm). The dropdown lists the lines the setup's "
                 "monet config declares; any other value can be typed in."))
    power_acq: float = _unit(unit='mW')
    power_nonacq: Optional[float] = _unit(None, 'mW')
    warmup_delay: float = _unit(0, 's')
    shutter_off_nonacq: bool = False
    lasers_off_finally: bool = False

    @model_validator(mode="after")
    def _default_nonacq_power(self):
        # The builder emits a non-acquisition 'set power' step; default it to
        # the acquisition power when not given so it is never None.
        if self.power_nonacq is None:
            self.power_nonacq = self.power_acq
        return self


class IlluSection(BaseModel):
    # No 'parameters': the monet config name comes from the chosen microscope
    # setup (not the design), and the old channel_group/filter/ROI were unused.
    model_config = _CFG
    enabled: bool = _field(
        True,
        tooltip="Include this subsystem when translating to the Run "
        "Sequence. Deselect to leave it out of the run.",
    )
    settings: IlluSettings


class ExperimentDesign(BaseModel):
    """Top-level experiment design (compiles to a Run Sequence)."""

    model_config = _CFG
    base_name: str
    save_dir: str = "."
    fluid: FluidSection
    img: ImgSection
    illu: Optional[IlluSection] = None


def validate_experiment_design(data) -> ExperimentDesign:
    """Validate an experiment-design dict, returning the parsed model.

    Parameters
    ----------
    data : dict
        The experiment design (e.g. parsed from YAML).

    Returns
    -------
    ExperimentDesign
        The validated model. Use ``model_dump(by_alias=True)`` to get a dict
        with the on-disk (hyphenated) keys back.

    Raises
    ------
    ExperimentDesignValidationError
        If validation fails.
    """
    try:
        return ExperimentDesign.model_validate(data)
    except ValidationError as exc:
        raise ExperimentDesignValidationError(
            "Experiment design validation failed:\n{}".format(exc)
        ) from exc
