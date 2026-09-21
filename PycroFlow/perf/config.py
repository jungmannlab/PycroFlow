"""Configuration for the WP-1 performance harness.

The whole harness is config-driven: frame rate, number of frames, circular
buffer size, the batch-size sweep list, and the output directories all come
from here (with sensible defaults). A config can be loaded from YAML
(:func:`load_config`) and individual knobs overridden from the command line
(:func:`apply_overrides`); the fully-resolved config is serialised into every
run's ``run_meta.json`` (:meth:`PerfConfig.to_dict`) so a run is reproducible.

Two directories are kept deliberately separate because the raw acquisition is
huge (a 40000-frame full-frame movie is tens of GB) while the analysis logs are
tiny:

``output_dir``
    Where the small, git-committed run directory lands (``run_meta.json`` +
    ``metrics.csv`` + ``buffer_timeseries.csv``). Defaults to ``results`` in
    the repo.
``data_dir``
    Where the raw NDTiff acquisition is written in ``instrument`` mode — point
    this at a **large data drive, not the repo**. WP-1 only needs the metrics,
    so the raw movie is deleted after each configuration is measured (set
    ``keep_raw_data`` to keep it). Unused in ``emulator`` mode (no frames are
    written to disk).

The circular-buffer size is expressed in **MB**, matching Micro-Manager's
"sequence buffer" setting; the equivalent capacity in frames is derived from
the frame size (ROI / full-frame dimensions x bytes per pixel).

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

# How the concurrent incremental reader runs (instrument mode):
#   "process" — a separate OS process reads the NDTiff off disk (the design-
#               intended isolation; no GIL / ZMQ-bridge sharing with acquisition)
#   "thread"  — a thread in the acquisition process (the more pessimistic
#               coupling; useful as a contrast).
# Emulator mode always uses the built-in simulated reader regardless.
READER_THREAD = "thread"
READER_PROCESS = "process"
VALID_READER_MODES = (READER_THREAD, READER_PROCESS)

# Micro-Manager reports its circular-buffer footprint in MB counted as
# mebibytes (1024 * 1024 bytes); use the same convention so the emulator's
# derived frame capacity matches what MM would report.
BYTES_PER_MB = 1024 * 1024


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
    buffer_mb : float
        Micro-Manager circular-buffer footprint, in MB (mebibytes). The
        equivalent capacity in frames is derived from the frame size.
    exposure_ms : float
        Camera exposure (instrument mode); recorded as provenance otherwise.
    roi : list[int] | None
        Camera ROI ``[x, y, w, h]``; ``None`` = full frame
        (``image_width`` x ``image_height``). The width/height also set the
        frame size used to convert ``buffer_mb`` to a frame capacity.
    image_width, image_height : int
        Full-frame dimensions, used for the frame size when ``roi`` is None.
    bytes_per_pixel : int
        Bytes per pixel (2 for 16-bit cameras) — for the frame-size / buffer
        capacity calculation.
    batch_sizes : list[int]
        Live-evaluation batch sizes to sweep (the reader reads this many
        contiguous frames at a time — never subsampled).
    monitor_interval_s : float
        How often the occupancy monitor samples the buffer.
    include_baseline : bool
        Whether to run a no-reader baseline in addition to the sweep.
    output_dir : str
        Base directory for the small, git-committed run dir.
    data_dir : str | None
        Where the raw NDTiff acquisition is written (instrument mode). Point
        at a large data drive, NOT the repo. Required in instrument mode.
    keep_raw_data : bool
        Keep the raw acquisition after measuring (default: delete it — WP-1
        only needs the metrics, and the movies are large).
    reader_mode : str
        How the concurrent reader runs in instrument mode: ``"process"`` (a
        separate OS process reading the NDTiff off disk — the design-intended
        isolation) or ``"thread"`` (in the acquisition process). Ignored in
        emulator mode (always simulated).
    reader_poll_s : float
        Poll interval of the separate-process reader while waiting for new
        frames / the dataset to appear.
    reader_reopen_interval_s : float
        Minimum seconds between re-opens of the growing NDTiff dataset by the
        separate-process reader. Re-opening a still-being-written dataset forces
        ndtiff to rebuild its index by scanning the TIFF IFDs (O(dataset size)),
        so the reader opens once and only re-opens — at most this often, and
        only when the on-disk index has actually grown — to pick up new frames.
    label : str
        Prefix for the run-dir name.
    emulator : EmulatorParams
        Emulated-model parameters (ignored in instrument mode).
    """

    mode: str = MODE_EMULATOR
    frame_rate_hz: float = 10.0
    n_frames: int = 2000
    buffer_mb: float = 4096.0
    exposure_ms: float = 100.0
    roi: list[int] | None = None
    image_width: int = 1024
    image_height: int = 1024
    bytes_per_pixel: int = 2
    batch_sizes: list[int] = field(default_factory=lambda: [1, 10, 100, 1000])
    monitor_interval_s: float = 0.05
    include_baseline: bool = True
    output_dir: str = "results"
    data_dir: str | None = None
    keep_raw_data: bool = False
    reader_mode: str = READER_PROCESS
    reader_poll_s: float = 0.05
    reader_reopen_interval_s: float = 2.0
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
        if self.buffer_mb <= 0:
            raise ValueError("buffer_mb must be > 0")
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("image_width / image_height must be > 0")
        if self.bytes_per_pixel <= 0:
            raise ValueError("bytes_per_pixel must be > 0")
        if not self.batch_sizes:
            raise ValueError("batch_sizes must be a non-empty list")
        if any(b <= 0 for b in self.batch_sizes):
            raise ValueError("every batch size must be > 0")
        if self.monitor_interval_s <= 0:
            raise ValueError("monitor_interval_s must be > 0")
        if self.reader_mode not in VALID_READER_MODES:
            raise ValueError(
                "reader_mode must be one of {}; got {!r}".format(
                    VALID_READER_MODES, self.reader_mode
                )
            )
        if self.reader_poll_s <= 0:
            raise ValueError("reader_poll_s must be > 0")
        if self.reader_reopen_interval_s <= 0:
            raise ValueError("reader_reopen_interval_s must be > 0")
        # Normalise container types (YAML may hand back tuples / ints).
        self.batch_sizes = [int(b) for b in self.batch_sizes]
        if self.roi is not None:
            self.roi = [int(v) for v in self.roi]
            if len(self.roi) != 4:
                raise ValueError("roi must be [x, y, w, h]")

    def frame_bytes(self) -> int:
        """Bytes per frame, from the ROI / full-frame size x bit depth."""
        if self.roi is not None:
            width, height = self.roi[2], self.roi[3]
        else:
            width, height = self.image_width, self.image_height
        return width * height * self.bytes_per_pixel

    def buffer_capacity_frames(self) -> int:
        """Circular-buffer capacity in frames derived from ``buffer_mb``."""
        return max(1, int(self.buffer_mb * BYTES_PER_MB // self.frame_bytes()))

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
