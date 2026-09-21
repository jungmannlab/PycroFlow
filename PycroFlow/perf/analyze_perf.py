"""Analyse WP-1 performance run dirs and draft the live-vs-batch go/no-go.

This is the step Claude runs *here* on the committed logs — it must not require
the instrument. It ingests one or more run directories (emulator and/or
instrument), compares every with-reader configuration against its same-mode
no-reader baseline, and emits:

``report.md``
    Human-readable tables + the go/no-go verdict + the thresholds used.
``report.json``
    Machine-readable verdict + per-configuration metrics and pass/fail.
``*.png`` (optional)
    Occupancy / dropped / throughput vs batch size and occupancy time series,
    written only when matplotlib is importable (it lives in the ``[hardware]``
    extra, absent on CI — the report is complete without the plots).

Run it with::

    python -m PycroFlow.perf.analyze_perf RUNDIR [RUNDIR ...] --out report_dir

The go/no-go rule and its thresholds are documented in
``docs/WP-1-perf-schema.md`` and echoed into every report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from PycroFlow.perf import schema

GO = "GO"
NO_GO = "NO-GO"
PENDING = "PENDING"


def _baseline_for(rows: list[dict]) -> dict | None:
    for row in rows:
        if not row["reader"]:
            return row
    return None


def evaluate_mode(rows: list[dict], thresholds: dict) -> dict:
    """Evaluate one mode's rows against the baseline and thresholds.

    Returns a dict with the baseline, per-configuration verdicts, and the
    mode-level verdict (GO iff every with-reader config passes).
    """
    baseline = _baseline_for(rows)
    base_thr = baseline["throughput_fps"] if baseline else 0.0
    base_drop = baseline["dropped_fraction"] if baseline else 0.0

    configs = []
    for row in rows:
        if not row["reader"]:
            continue
        buffer_frames = row["buffer_frames"] or 1
        occ_frac = row["occupancy_peak"] / buffer_frames
        retention = row["throughput_fps"] / base_thr if base_thr > 0 else 0.0
        dropped_ok = (
            row["dropped_fraction"] <= thresholds["max_dropped_fraction"]
        )
        tput_ok = retention >= thresholds["min_throughput_retention"]
        occ_ok = occ_frac <= thresholds["max_occupancy_fraction"]
        passed = dropped_ok and tput_ok and occ_ok
        configs.append(
            {
                "batch_size": row["batch_size"],
                "occupancy_peak": row["occupancy_peak"],
                "occupancy_fraction": round(occ_frac, 4),
                "dropped_fraction": row["dropped_fraction"],
                "extra_dropped_fraction": round(
                    row["dropped_fraction"] - base_drop, 6
                ),
                "throughput_fps": row["throughput_fps"],
                "throughput_retention": round(retention, 4),
                "dropped_ok": dropped_ok,
                "throughput_ok": tput_ok,
                "occupancy_ok": occ_ok,
                "passed": passed,
            }
        )

    if baseline is None or not configs:
        verdict = PENDING
    elif all(c["passed"] for c in configs):
        verdict = GO
    else:
        verdict = NO_GO
    return {
        "baseline": baseline,
        "configs": configs,
        "verdict": verdict,
    }


def _run_label(meta: dict) -> str:
    """Short human label for a run dir from its metadata."""
    cfg = meta.get("config", {}) or {}
    roi = cfg.get("roi")
    if roi:
        size = "{}x{}".format(roi[2], roi[3])
    else:
        size = "{}x{}".format(cfg.get("image_width"), cfg.get("image_height"))
    return "{} frames, {} px, buffer {:.0f} MB".format(
        meta.get("n_frames"), size, float(meta.get("buffer_mb", 0) or 0)
    )


def _aggregate_verdict(verdicts: list[str]) -> str:
    """Combine per-run verdicts: any NO-GO dominates, then PENDING."""
    if not verdicts:
        return PENDING
    if NO_GO in verdicts:
        return NO_GO
    if PENDING in verdicts:
        return PENDING
    return GO


def analyze(
    run_dirs: list[str],
    out_dir: str,
    thresholds: dict | None = None,
) -> dict:
    """Analyse run dirs, write report artifacts, return the report dict.

    Each run directory is evaluated **independently against its own no-reader
    baseline** (so runs at different configs / frame counts don't cross-
    contaminate), then aggregated per mode for the overall verdict.
    """
    thresholds = thresholds or dict(schema.DEFAULT_THRESHOLDS)
    os.makedirs(out_dir, exist_ok=True)

    loaded = [schema.load_run_dir(d) for d in run_dirs]

    # Evaluate each run dir on its own (a run dir usually holds one mode's
    # baseline + sweep).
    runs: list[dict] = []
    for run_dir, run in zip(run_dirs, loaded):
        by_mode: dict[str, list[dict]] = {}
        for row in run["metrics"]:
            by_mode.setdefault(row["mode"], []).append(row)
        for mode, rows in by_mode.items():
            ev = evaluate_mode(rows, thresholds)
            runs.append(
                {
                    "run_dir": os.path.abspath(run_dir),
                    "mode": mode,
                    "label": _run_label(run["meta"]),
                    "baseline": ev["baseline"],
                    "configs": ev["configs"],
                    "verdict": ev["verdict"],
                    "meta": run["meta"],
                    "timeseries": run["timeseries"],
                }
            )

    # Aggregate per mode (baseline / configs kept for backward-compatible
    # single-run reporting).
    modes: dict[str, dict] = {}
    for mode in sorted({r["mode"] for r in runs}):
        mruns = [r for r in runs if r["mode"] == mode]
        modes[mode] = {
            "verdict": _aggregate_verdict([r["verdict"] for r in mruns]),
            "baseline": mruns[0]["baseline"],
            "configs": [c for r in mruns for c in r["configs"]],
            "run_dirs": [r["run_dir"] for r in mruns],
        }

    overall = _overall_verdict(modes)
    report = {
        "schema_version": schema.SCHEMA_VERSION,
        "run_dirs": [os.path.abspath(d) for d in run_dirs],
        "thresholds": thresholds,
        "runs": [
            {k: r[k] for k in ("run_dir", "mode", "label", "verdict")}
            for r in runs
        ],
        "modes": modes,
        "overall_verdict": overall,
        "recommendation": _recommendation(overall),
    }

    plots = _write_plots(runs, out_dir)
    report["plots"] = plots

    with open(
        os.path.join(out_dir, "report.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(_render_markdown(report, runs))
    return report


def _overall_verdict(modes: dict) -> str:
    verdicts = {m["verdict"] for m in modes.values()}
    if not verdicts:
        return PENDING
    if NO_GO in verdicts:
        return NO_GO
    # Live streaming only clears once the instrument round-trip is GO.
    if "instrument" not in modes or modes["instrument"]["verdict"] != GO:
        return PENDING
    return GO


def _recommendation(overall: str) -> str:
    if overall == GO:
        return (
            "Adopt live incremental NDTiff reading (option b) as the primary "
            "path: the concurrent reader did not compromise acquisition."
        )
    if overall == NO_GO:
        return (
            "Do NOT adopt live streaming as-is: the concurrent reader "
            "compromised acquisition. Fall back to post-FOV batch processing "
            "(or the watchdog, option c) and investigate the contention."
        )
    return (
        "Emulator dry run only (or no instrument GO yet): the code path is "
        "proven, but the live-vs-batch go/no-go clears only after the "
        "--instrument round-trip on the acquisition PC is analysed here."
    )


def _fmt(value) -> str:
    if isinstance(value, float):
        return "{:.4g}".format(value)
    return str(value)


def _render_markdown(report: dict, runs: list[dict]) -> str:
    thr = report["thresholds"]
    lines = ["# WP-1 live-reader performance — go/no-go report", ""]
    lines.append("**Overall verdict: {}**".format(report["overall_verdict"]))
    lines.append("")
    lines.append(report["recommendation"])
    lines.append("")

    lines.append("## Verdict by mode")
    lines.append("")
    lines.append("| mode | verdict |")
    lines.append("|---|---|")
    for mode, result in sorted(report["modes"].items()):
        lines.append("| {} | {} |".format(mode, result["verdict"]))
    lines.append("")

    lines.append("## Thresholds")
    lines.append("")
    lines.append("| threshold | value |")
    lines.append("|---|---|")
    lines.append(
        "| max dropped fraction | {} |".format(thr["max_dropped_fraction"])
    )
    lines.append(
        "| min throughput retention | {} |".format(
            thr["min_throughput_retention"]
        )
    )
    lines.append(
        "| max peak occupancy fraction | {} |".format(
            thr["max_occupancy_fraction"]
        )
    )
    lines.append("")

    # One table per run dir, each against its own baseline.
    for run in runs:
        lines.append(
            "## {} — {} ({})".format(
                os.path.basename(run["run_dir"]),
                run["verdict"],
                run["mode"],
            )
        )
        lines.append("")
        lines.append("_{}_".format(run["label"]))
        lines.append("")
        base = run["baseline"]
        if base is not None:
            lines.append(
                "Baseline (no reader): throughput {} fps, peak occupancy "
                "{}, dropped fraction {}.".format(
                    _fmt(base["throughput_fps"]),
                    base["occupancy_peak"],
                    _fmt(base["dropped_fraction"]),
                )
            )
            lines.append("")
        lines.append(
            "| batch | peak occ | occ frac | dropped frac | throughput "
            "fps | retention | pass |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for cfg in run["configs"]:
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} |".format(
                    cfg["batch_size"],
                    cfg["occupancy_peak"],
                    _fmt(cfg["occupancy_fraction"]),
                    _fmt(cfg["dropped_fraction"]),
                    _fmt(cfg["throughput_fps"]),
                    _fmt(cfg["throughput_retention"]),
                    "yes" if cfg["passed"] else "NO",
                )
            )
        lines.append("")

    if report.get("plots"):
        lines.append("## Plots")
        lines.append("")
        for plot in report["plots"]:
            lines.append("![{0}]({0})".format(plot))
        lines.append("")
    else:
        lines.append(
            "_Plots skipped (matplotlib not installed); the tables above "
            "are the full result._"
        )
        lines.append("")

    lines.append("## Provenance")
    lines.append("")
    for run in runs:
        meta = run["meta"]
        lines.append(
            "- `{}`: mode `{}` on `{}` ({}), pycroflow {}, git {}, "
            "{} → {}".format(
                os.path.basename(run["run_dir"]),
                meta.get("mode"),
                meta.get("host"),
                meta.get("os"),
                meta.get("pycroflow_version"),
                str(meta.get("git_commit"))[:12],
                meta.get("utc_start"),
                meta.get("utc_end"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _slug(run_dir: str) -> str:
    return os.path.basename(run_dir.rstrip("/\\")) or "run"


def _write_plots(runs: list[dict], out_dir: str) -> list[str]:
    """Write per-run PNG plots if matplotlib is available; else empty list."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    written: list[str] = []
    for run in runs:
        reader_rows = sorted(
            (c for c in run["configs"]),
            key=lambda c: c["batch_size"],
        )
        if not reader_rows:
            continue
        slug = _slug(run["run_dir"])
        batches = [c["batch_size"] for c in reader_rows]
        base = run["baseline"]

        fig, axes = plt.subplots(1, 3, figsize=(13, 4))
        axes[0].plot(
            batches,
            [c["occupancy_peak"] for c in reader_rows],
            "o-",
            label="with reader",
        )
        if base is not None:
            axes[0].axhline(
                base["occupancy_peak"],
                color="k",
                ls="--",
                label="baseline",
            )
        axes[0].set_title("peak buffer occupancy")
        axes[0].set_xlabel("batch size")
        axes[0].set_ylabel("frames")
        axes[0].set_xscale("log")
        axes[0].legend()

        axes[1].plot(
            batches,
            [c["dropped_fraction"] for c in reader_rows],
            "o-",
        )
        axes[1].set_title("dropped fraction")
        axes[1].set_xlabel("batch size")
        axes[1].set_xscale("log")

        axes[2].plot(
            batches,
            [c["throughput_fps"] for c in reader_rows],
            "o-",
            label="with reader",
        )
        if base is not None:
            axes[2].axhline(
                base["throughput_fps"],
                color="k",
                ls="--",
                label="baseline",
            )
        axes[2].set_title("write throughput (fps)")
        axes[2].set_xlabel("batch size")
        axes[2].set_xscale("log")
        axes[2].legend()

        fig.suptitle("WP-1 sweep — {} ({})".format(slug, run["label"]))
        fig.tight_layout()
        name = "sweep_{}.png".format(slug)
        fig.savefig(os.path.join(out_dir, name), dpi=110)
        plt.close(fig)
        written.append(name)

        # Occupancy time series per configuration (this run's own series).
        ts_rows = run.get("timeseries", [])
        if ts_rows:
            fig2, ax = plt.subplots(figsize=(8, 4))
            groups: dict[tuple, list[dict]] = {}
            for row in ts_rows:
                key = (row["reader"], row["batch_size"])
                groups.setdefault(key, []).append(row)
            for (rdr, batch), series in sorted(groups.items()):
                series.sort(key=lambda r: r["frame_index"])
                label = (
                    "baseline" if not rdr else "reader batch={}".format(batch)
                )
                ax.plot(
                    [r["frame_index"] for r in series],
                    [r["occupancy"] for r in series],
                    label=label,
                    lw=1,
                )
            ax.set_title("buffer occupancy vs frame index — {}".format(slug))
            ax.set_xlabel("frame index")
            ax.set_ylabel("occupancy (frames)")
            ax.legend(fontsize=8)
            fig2.tight_layout()
            name2 = "timeseries_{}.png".format(slug)
            fig2.savefig(os.path.join(out_dir, name2), dpi=110)
            plt.close(fig2)
            written.append(name2)
    return written


def build_parser() -> argparse.ArgumentParser:
    """Build the analysis argument parser."""
    parser = argparse.ArgumentParser(
        prog="pycroflow-perf-analyze",
        description=(
            "Analyse one or more WP-1 performance run dirs and draft the "
            "live-vs-batch go/no-go report."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="One or more run directories to analyse.",
    )
    parser.add_argument(
        "--out",
        default="wp1_report",
        help="Directory to write report.md / report.json / plots into.",
    )
    parser.add_argument(
        "--max-dropped-fraction",
        type=float,
        default=None,
        help="Override the max dropped-fraction threshold.",
    )
    parser.add_argument(
        "--min-throughput-retention",
        type=float,
        default=None,
        help="Override the min throughput-retention threshold.",
    )
    parser.add_argument(
        "--max-occupancy-fraction",
        type=float,
        default=None,
        help="Override the max peak-occupancy-fraction threshold.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, analyse the run dirs, print the verdict."""
    parser = build_parser()
    args = parser.parse_args(argv)
    thresholds = dict(schema.DEFAULT_THRESHOLDS)
    if args.max_dropped_fraction is not None:
        thresholds["max_dropped_fraction"] = args.max_dropped_fraction
    if args.min_throughput_retention is not None:
        thresholds["min_throughput_retention"] = args.min_throughput_retention
    if args.max_occupancy_fraction is not None:
        thresholds["max_occupancy_fraction"] = args.max_occupancy_fraction

    report = analyze(args.run_dirs, args.out, thresholds)
    print("Overall verdict: {}".format(report["overall_verdict"]))
    print(report["recommendation"])
    for mode, result in sorted(report["modes"].items()):
        print("  mode {}: {}".format(mode, result["verdict"]))
    print("Report written to: {}".format(os.path.abspath(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
