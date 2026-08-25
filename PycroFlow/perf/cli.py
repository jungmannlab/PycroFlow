"""Command-line entry point for the WP-1 performance harness.

Non-interactive and Windows-friendly: one command runs the full batch-size
sweep for a mode and writes a single timestamped run directory. Run it as
either the console script (``pycroflow-perf``) or ``python -m PycroFlow.perf``.

Examples
--------
Dry run on the emulator (what CI and the pre-instrument check use)::

    python -m PycroFlow.perf --emulator

Real acquisition on the Windows acquisition PC (see docs/WP-1-RUNBOOK.md)::

    python -m PycroFlow.perf --instrument --config PycroFlow/perf/configs/wp1_default.yaml

Every knob has a default and is documented in ``--help``.
"""

from __future__ import annotations

import argparse
import sys

from PycroFlow.perf.config import (
    MODE_EMULATOR,
    MODE_INSTRUMENT,
    apply_overrides,
    load_config,
)
from PycroFlow.perf.harness import run_and_write


def _parse_batch_sizes(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [int(v) for v in value.split(",") if v.strip()]


def _parse_roi(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [int(v) for v in value.split(",") if v.strip()]


def _parse_image_size(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = value.lower().replace("x", ",").split(",")
    nums = [int(p) for p in parts if p.strip()]
    if len(nums) != 2:
        raise ValueError("--image-size must be 'WxH', e.g. '1024x1024'")
    return nums[0], nums[1]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (every knob is documented here)."""
    parser = argparse.ArgumentParser(
        prog="pycroflow-perf",
        description=(
            "WP-1 live-reader performance harness: benchmark acquisition "
            "with and without a concurrent incremental NDTiff reader, "
            "sweeping over live-evaluation batch sizes. Writes one "
            "timestamped run directory (run_meta.json + metrics.csv + "
            "buffer_timeseries.csv). Analyse it with "
            "'python -m PycroFlow.perf.analyze_perf'."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--emulator",
        dest="mode",
        action="store_const",
        const=MODE_EMULATOR,
        help="Frame source: the PycroFlow emulator (no instrument).",
    )
    mode.add_argument(
        "--instrument",
        dest="mode",
        action="store_const",
        const=MODE_INSTRUMENT,
        help="Frame source: a real acquisition on the acquisition PC.",
    )
    parser.set_defaults(mode=None)

    parser.add_argument(
        "--config",
        default=None,
        help="YAML config path; omit to use built-in defaults.",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        default=None,
        help="Base output directory for the small, git-committed run dir.",
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Where the raw NDTiff acquisition is written (instrument "
        "mode). Point at a large data drive, NOT the repo. Required in "
        "instrument mode; the raw movie is deleted after measurement "
        "unless --keep-raw-data.",
    )
    parser.add_argument(
        "--keep-raw-data",
        dest="keep_raw_data",
        action="store_true",
        default=None,
        help="Keep the raw acquisition instead of deleting it after "
        "measurement (instrument mode; movies are large).",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Prefix for the run-dir name.",
    )
    parser.add_argument(
        "--frames",
        dest="n_frames",
        type=int,
        default=None,
        help="Number of frames per configuration.",
    )
    parser.add_argument(
        "--frame-rate",
        dest="frame_rate_hz",
        type=float,
        default=None,
        help="Target frame rate (Hz).",
    )
    parser.add_argument(
        "--buffer-mb",
        dest="buffer_mb",
        type=float,
        default=None,
        help="Micro-Manager circular-buffer footprint in MB (mebibytes).",
    )
    parser.add_argument(
        "--image-size",
        default=None,
        help="Full-frame size as 'WxH' (e.g. '1024x1024'); sets the frame "
        "size used to convert the buffer MB to frames.",
    )
    parser.add_argument(
        "--batch-sizes",
        default=None,
        help="Comma-separated live-evaluation batch sizes, e.g. '1,8,32'.",
    )
    parser.add_argument(
        "--exposure-ms",
        dest="exposure_ms",
        type=float,
        default=None,
        help="Camera exposure in ms (instrument mode).",
    )
    parser.add_argument(
        "--roi",
        default=None,
        help="Camera ROI as 'x,y,w,h' (instrument mode).",
    )
    parser.add_argument(
        "--monitor-interval",
        dest="monitor_interval_s",
        type=float,
        default=None,
        help="Occupancy sampling interval (s).",
    )
    parser.add_argument(
        "--no-baseline",
        dest="include_baseline",
        action="store_false",
        default=None,
        help="Skip the no-reader baseline run.",
    )

    emu = parser.add_argument_group(
        "emulator model (ignored in --instrument mode)"
    )
    emu.add_argument(
        "--emu-write-speed",
        dest="emu_write_speed_factor",
        type=float,
        default=None,
        help="Writer drain rate as a multiple of the frame rate.",
    )
    emu.add_argument(
        "--emu-contention",
        dest="emu_contention",
        type=float,
        default=None,
        help="Writer slow-down factor while a batch is being read.",
    )
    emu.add_argument(
        "--emu-read-cost",
        dest="emu_read_cost_per_frame_s",
        type=float,
        default=None,
        help="Emulated reader cost per frame (s); raise to model a slow "
        "reader.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the sweep, and print the run-dir path."""
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    emu = {
        "write_speed_factor": args.emu_write_speed_factor,
        "contention": args.emu_contention,
        "read_cost_per_frame_s": args.emu_read_cost_per_frame_s,
    }
    image_size = _parse_image_size(args.image_size)
    cfg = apply_overrides(
        cfg,
        mode=args.mode,
        output_dir=args.output_dir,
        data_dir=args.data_dir,
        keep_raw_data=args.keep_raw_data,
        label=args.label,
        n_frames=args.n_frames,
        frame_rate_hz=args.frame_rate_hz,
        buffer_mb=args.buffer_mb,
        image_width=image_size[0] if image_size else None,
        image_height=image_size[1] if image_size else None,
        batch_sizes=_parse_batch_sizes(args.batch_sizes),
        exposure_ms=args.exposure_ms,
        roi=_parse_roi(args.roi),
        monitor_interval_s=args.monitor_interval_s,
        include_baseline=args.include_baseline,
        emulator=emu,
    )

    print(
        "Running WP-1 perf sweep: mode={} frames={} rate={}Hz "
        "buffer={}MB ({} frames) batches={}".format(
            cfg.mode,
            cfg.n_frames,
            cfg.frame_rate_hz,
            cfg.buffer_mb,
            cfg.buffer_capacity_frames(),
            cfg.batch_sizes,
        )
    )
    run_dir = run_and_write(cfg)
    print("Wrote run dir: {}".format(run_dir))
    print(
        "Analyse with: python -m PycroFlow.perf.analyze_perf {}".format(
            run_dir
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
