"""Configuration for the WP-1 performance harness.

The whole harness is config-driven: frame rate, number of frames, circular
buffer size, the batch-size sweep list, and the output directory all come from
here (with sensible defaults). A config can be loaded from YAML
(:func:`load_config`) and individual knobs overridden from the command line
(:func:`apply_overrides`); the fully-resolved config is serialised into every
run's ``run_meta.json`` (:meth:`PerfConfig.to_dict`) so a run is reproducible.

Only the *frame source* differs between ``emulator`` and ``instrument`` mode —
see :mod:`PycroFlow.perf.backends`. The ``emulator`` sub-parameters
(:class:`EmulatorParams`) drive the pure-stdlib producer/consumer simulation
and have no effect in ``instrument`` mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import yaml

MODE_EMULATOR = "emulator"
MODE_INSTRUMENT = "instrument"
VALID_MODES = (MODE_EMULATOR, MODE_INSTRUMENT)


@dataclass
class EmulatorParams:
    """Parameters of the emulated producer/consumer/bounded-buffer model.

    The emulated writer drains the buffer ``write_speed_factor`` times faster
    than the producer fills it, so a *baseline* (no-reader) run keeps the
    buffer near-empty. While the incremental reader is reading a batch, the
    writer is slowed by ``contention`` (its per-frame cost is multiplied by
    ``1 + contention``), modelling I/O / CPU contention between the reader and
    the save pipeline. A *slow* reader (large ``read_cost_per_frame_s``) holds
    that contention on for longer, so larger batches produce longer bursts of
    back-pressure — exactly the effect the harness must be able to detect.

    Defaults model the *expected* real outcome (the page cache serves the
    just-written frames, so contention is negligible); the backpressure test
    overrides them to induce and detect back-pressure.

    Parameters
    ----------
    write_speed_factor : float
        Writer drain rate as a multiple of the producer/frame rate. Must be
        ``> 1`` for a keep-up baseline.
    contention : float
        Fractional slow-down applied to the writer while a batch is being
        read (``0`` = the reader is free, the expected page-cache outcome).
    read_cost_per_frame_s : float
        Wall-clock cost the emulated reader spends per frame in a batch.
    tick_s : float
        Engine integration tick. The model integrates over the *measured*
        elapsed time each tick, so it stays correct under coarse OS timers
        (e.g. Windows CI); the tick only sets the sampling granularity.
    """

    write_speed_factor: float = 3.0
    contention: float = 0.1
    read_cost_per_frame_s: float = 0.0002
    tick_s: float = 0.001


@dataclass
class PerfConfig:
    """Top-level harness configuration.

    Parameters
    ----------
    mode : str
        ``"emulator"`` (dry run, no instrument) or ``"instrument"`` (real
        acquisition on the PC). Only the frame source differs.
    frame_rate_hz : float
        Target acquisition frame rate.
    n_frames : int
        Number of frames per configuration.
    buffer_size : int
        Micro-Manager circular-buffer capacity, in frames.
    batch_sizes : list[int]
        Live-evaluation batch sizes to sweep (the reader reads this many
        contiguous frames at a time — never subsampled).
    exposure_ms : float
        Camera exposure (instrument mode); recorded as provenance otherwise.
    roi : list[int] | None
        Optional camera ROI ``[x, y, w, h]`` (instrument mode).
    monitor_interval_s : float
        How often the occupancy monitor samples the buffer.
    include_baseline : bool
        Whether to run a no-reader baseline in addition to the sweep.
    output_dir : str
        Base directory under which the timestamped run dir is created.
    label : str
        Prefix for the run-dir name.
    emulator : EmulatorParams
        Emulated-model parameters (ignored in instrument mode).
    """

    mode: str = MODE_EMULATOR
    frame_rate_hz: float = 100.0
    n_frames: int = 2000
    buffer_size: int = 200
    batch_sizes: list[int] = field(default_factory=lambda: [1, 8, 32, 128])
    exposure_ms: float = 100.0
    roi: list[int] | None = None
    monitor_interval_s: float = 0.01
    include_baseline: bool = True
    output_dir: str = "results"
    label: str = "wp1"
    emulator: EmulatorParams = field(default_factory=EmulatorParams)

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(
                "mode must be one of {}; got {!r}".format(
                    VALID_MODES, self.mode
                )
            )
        if self.frame_rate_hz <= 0:
            raise ValueError("frame_rate_hz must be > 0")
        if self.n_frames <= 0:
            raise ValueError("n_frames must be > 0")
        if self.buffer_size <= 0:
            raise ValueError("buffer_size must be > 0")
        if not self.batch_sizes:
            raise ValueError("batch_sizes must be a non-empty list")
        if any(b <= 0 for b in self.batch_sizes):
            raise ValueError("every batch size must be > 0")
        if self.monitor_interval_s <= 0:
            raise ValueError("monitor_interval_s must be > 0")
        # Normalise container types (YAML may hand back tuples / ints).
        self.batch_sizes = [int(b) for b in self.batch_sizes]
        if self.roi is not None:
            self.roi = [int(v) for v in self.roi]

    def to_dict(self) -> dict:
        """Return a plain-dict, JSON-serialisable copy for ``run_meta``."""
        return asdict(self)


def _split_known(data: dict) -> tuple[dict, dict]:
    """Split ``data`` into (top-level fields, emulator sub-fields)."""
    data = dict(data)
    emu = data.pop("emulator", None) or {}
    return data, dict(emu)


def config_from_dict(data: dict) -> PerfConfig:
    """Build a :class:`PerfConfig` from a plain dict (e.g. parsed YAML)."""
    top, emu = _split_known(data)
    if emu:
        top["emulator"] = EmulatorParams(**emu)
    return PerfConfig(**top)


def load_config(path: str | None) -> PerfConfig:
    """Load a :class:`PerfConfig` from a YAML file, or return defaults.

    Parameters
    ----------
    path : str or None
        Path to a YAML config, or ``None`` to use all defaults.
    """
    if path is None:
        return PerfConfig()
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            "config file {!r} must contain a mapping".format(path)
        )
    return config_from_dict(data)


def apply_overrides(cfg: PerfConfig, **overrides) -> PerfConfig:
    """Return a copy of ``cfg`` with the given non-``None`` fields replaced.

    Emulator sub-parameters are passed as an ``emulator`` mapping and merged
    onto the existing :class:`EmulatorParams`.
    """
    emu_over = overrides.pop("emulator", None)
    clean = {k: v for k, v in overrides.items() if v is not None}
    cfg = replace(cfg, **clean)
    if emu_over:
        merged = {k: v for k, v in emu_over.items() if v is not None}
        cfg = replace(cfg, emulator=replace(cfg.emulator, **merged))
    return cfg
