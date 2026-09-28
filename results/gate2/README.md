# `results/gate2/` — committed Gate-2 verdict logs

This directory holds the machine-readable output of the **Gate-2 harness**
(`PycroFlow/perf/gate2_harness.py`) — the heavy on-instrument integration gate
for the WP-4 live-analysis slice (WP-4 + pycromanager-1.0 + WP-GUI, bundled on
`feature/gate2-bundle`). It is the hand-off point of the Gate-2 round-trip:

1. Claude **builds + emulator-validates** the harness in the dev container.
2. You **run it on the Windows acquisition PC** in instrument mode (see below),
   which writes a timestamped verdict here (`instrument_<UTC-timestamp>.json`).
3. You **commit that JSON** to `feature/gate2-bundle` and push.
4. Claude **analyses** the committed verdict here and drafts the Gate-2 go/no-go.

## Running it

Hermetic dry run (the dev container / CI — no hardware, no network):

```
python -m PycroFlow.perf.gate2_harness --mode emulator
```

On the acquisition PC (real MDA + real monet interlock):

```
python -m PycroFlow.perf.gate2_harness --mode instrument ^
    --data-dir D:\gate2_raw ^
    --n-frames 2000 ^
    --mm-config C:\path\to\MMConfig.cfg
```

Add `--registry-url http://<host>:<port> --registry-token <tok>` to post the
per-FOV record to the **real** picasso-registry instead of the in-memory mock.
Every knob has a default and is documented in `--help`; the run is fully
non-interactive.

## What the verdict contains

One JSON object per run:

- `gate2_pass` — the overall boolean.
- `version_block` — resolved `numpy` (+ `np.trapezoid`), `picasso`,
  `pycromanager`, `pycroflow`, git commit; in instrument mode also the MM
  nightly + `ndtiff` / `ndstorage`.
- `checks[]` — one `{name, criterion, passed, detail, values}` per Gate-2
  criterion.
- `failures[]` — the failed checks with context (empty on a pass).

### Criterion → check map

| # | Gate-2 criterion | check `name` |
|---|------------------|--------------|
| 1 | acquisition uncompromised / reader keeps up (no drops, bounded queue) | `keep_up` |
| 2 | no silent subsample — coverage block reconciles (`frames_read == frames_localized`, `partial=False`, `run_id` present) | `no_silent_subsample` |
| 3 | live NeNA / locs-per-frame / background are real (non-None) | `live_metrics_real` |
| 4 | one per-FOV record reaches the registry + its coverage reconciles | `registry_record` |
| 5 | **T3 laser interlock on all three exits** (normal / abort / injected exception) — lasers DISABLED + shutter CLOSED (safety-critical) | `laser_interlock` |
| 6 | pycromanager-1.0 acq-PC items (NDTiff-v3 tail, RAM-peek, numpy-2 coexistence) — *instrument-only; skipped-pass in emulator* | `pycromanager_1_0` |
| 7 | write-target-gated archive move + checksum-before-delete (`verified=True`) | `archive` |

Emulator dry-run verdicts (`emulator_*.json`) are throwaway and normally not
committed; commit the `instrument_*.json` verdicts that carry the real
on-hardware numbers. `sample_emulator.json` is a checked-in example verdict from
a hermetic run so the JSON shape is reviewable without running the harness.
