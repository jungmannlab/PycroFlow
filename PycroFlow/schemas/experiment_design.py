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
_CFG = ConfigDict(populate_by_name=True, extra='allow')


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
    adapter: str
    adapter_incubation: float


class TargetRound(BaseModel):
    """Per-target parameters for an SPH-RESI run."""
    model_config = _CFG
    bc_imager_pre: str = Field(alias='BC_imager_pre')
    frames_bc_pre: int = Field(alias='frames_BC_pre')
    bc_imager_post: str = Field(alias='BC_imager_post')
    frames_bc_post: int = Field(alias='frames_BC_post')
    resi_imager: str = Field(alias='RESI-imager')
    resi_frames: int = Field(alias='RESI-frames')
    resi_rounds: List[ResiRound] = Field(alias='RESI-rounds')


class Round0(BaseModel):
    """Optional pre-target imaging round (e.g. alignment structures)."""
    model_config = _CFG
    round0_imager: str
    frames_round0: int


# --- experiment-type design blocks (discriminated on ``type``) -----------

class ExchangeExperiment(BaseModel):
    """Exchange-PAINT experiment design."""
    model_config = _CFG
    type: Literal['Exchange']
    wash_buffer: str
    imagers: List[str] = Field(default_factory=list)
    initial_imager: Optional[str] = None


class SphResiExperiment(BaseModel):
    """SPH-RESI experiment design."""
    model_config = _CFG
    type: Literal['SPH-RESI']
    wash_buffer_1: str
    wash_buffer_2: Optional[str] = None
    blocker: str
    blocker_incubation: float
    initial_imager_present: bool = False
    round0: Optional[Round0]
    target_rounds: Dict[str, TargetRound] = Field(alias='target-rounds')


ExperimentBlock = Annotated[
    Union[ExchangeExperiment, SphResiExperiment],
    Field(discriminator='type'),
]


# --- fluid / img / illu sections -----------------------------------------

class FluidParameters(BaseModel):
    """Per-run fluid driver parameters (passed through to the Run Sequence)."""
    model_config = _CFG
    start_velocity: float = 500
    max_velocity: float = 10000
    stop_velocity: float = 500
    pumpout_dispense_velocity: float = 290000
    clean_velocity: float = 10000
    clean_delay: float = 0
    mode: str = 'tubing_ignore'
    extractionfactor: float = 1
    inject_pickup_extravol: float = 0
    inject_in_to_out_delay: float = 0
    inject_out_to_in_delay: float = 0
    inject_precreate_underpressure: bool = False


class FluidSettings(BaseModel):
    """Experiment-level fluid settings + the experiment-type design block."""
    model_config = _CFG
    vol_wash: float
    vol_reagent: Optional[float] = None
    vol_imager_post: Optional[float] = None
    vol_remove_before_flush: float = 0
    wait_after_pickup: float = 0
    wash_buffer_1: Optional[str] = None
    wash_buffer_2: Optional[str] = None
    reservoir_names: Dict[int, str]
    special_names: Dict[str, int] = Field(default_factory=dict)
    cleaning_reservoirs: List[Union[int, str]] = Field(default_factory=list)
    experiment: ExperimentBlock


class FluidSection(BaseModel):
    model_config = _CFG
    parameters: FluidParameters = Field(default_factory=FluidParameters)
    settings: FluidSettings


class ImgParameters(BaseModel):
    model_config = _CFG
    show_progress: bool = True
    show_display: bool = True
    close_display_after_acquisition: bool = True


class ImgSettings(BaseModel):
    model_config = _CFG
    t_exp: float
    # frames may be a single count or a per-imager mapping (Exchange).
    frames: Optional[Union[int, Dict[str, int]]] = None
    darkframes: Optional[int] = None


class ImgSection(BaseModel):
    model_config = _CFG
    parameters: ImgParameters = Field(default_factory=ImgParameters)
    settings: ImgSettings


class IlluParameters(BaseModel):
    model_config = _CFG
    setup: Optional[str] = None
    channel_group: Optional[str] = None
    filter: Optional[str] = None
    ROI: Optional[List[int]] = None


class IlluSettings(BaseModel):
    model_config = _CFG
    laser: int
    power_acq: float
    power_nonacq: Optional[float] = None
    warmup_delay: float = 0
    shutter_off_nonacq: bool = False
    lasers_off_finally: bool = False

    @model_validator(mode='after')
    def _default_nonacq_power(self):
        # The builder emits a non-acquisition 'set power' step; default it to
        # the acquisition power when not given so it is never None.
        if self.power_nonacq is None:
            self.power_nonacq = self.power_acq
        return self


class IlluSection(BaseModel):
    model_config = _CFG
    parameters: IlluParameters = Field(default_factory=IlluParameters)
    settings: IlluSettings


class ExperimentDesign(BaseModel):
    """Top-level experiment design (compiles to a Run Sequence)."""
    model_config = _CFG
    base_name: str
    save_dir: str = '.'
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
