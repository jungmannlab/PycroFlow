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

On the acquisition PC (real MDA + real monet interlock). **The MM 2.0 GUI must
be running** (the harness connects to its ZMQ core) and `--monet-setup` must name
this microscope's monet config (a `monet.CONFIGS` key) so the T3 interlock can
control the lasers:

```
python -m PycroFlow.perf.gate2_harness --mode instrument ^
    --data-dir D:\gate2_raw ^
    --n-frames 2000 ^
    --exposure-ms 100 ^
    --laser 488 ^
    --monet-setup <your-monet-config-name> ^
    --archive-dir \\pool\archive\gate2 ^
    --mm-config C:\path\to\MMConfig.cfg
```

The harness **drives the acquisition itself** in instrument mode: it starts a
real `multi_d_acquisition_events` MDA (via PycroFlow's shared MM Core) in a
background thread writing NDTiff/OME-TIFF into `--data-dir`, turns the imaging
laser (`--laser <line>`, e.g. 488/561) ON **via the public illumination API**,
and the WP-4 tiff-tail source live-reads that movie while it is being written.
The T3 interlock turns the laser off at the end/abort/crash through the same
public `IlluminationSystem.all_off()` primitive (which runs monet's lazy init
first — so it works even before any other illumination command). Omit `--laser`
and no laser is enabled (a warning prints); live-localize may then see no signal.

Add `--registry-url http://<host>:<port> --registry-token <tok>` to post the
per-FOV record to the **real** picasso-registry (requests-based `RegistryClient`,
works on a `[client]`-only install) instead of the harness's built-in
fastapi-free in-memory stub.

**Watchdog / no-hang guarantee.** `--timeout <s>` (default 900) caps the clean
FOV. On timeout the harness (a) **fires the T3 interlock first** — lasers off +
shutter closed, never left hot on a stall — then (b) dumps every thread's
traceback to stderr (so you see where it stalled), (c) tears the acquisition
down, and (d) writes a verdict with `stalled: true` / `stalled_phase` and exits
non-zero. A `faulthandler` hard-deadline dump is also armed as a last-ditch
self-report. It will **never silently hang** again.

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

## Acq-PC checklist — what only hardware can confirm

The container has no Micro-Manager, so the real-MDA leg is built best-effort here
and validated by you on the acquisition PC. When you run `--mode instrument`,
confirm:

1. **Acquisition actually runs.** A `gate2_raw` dataset appears under
   `--data-dir` and grows during the run (the driver's MDA is writing frames).
   The 60-min hang was exactly this step never happening — if no folder appears,
   the harness now times out, dumps tracebacks, and exits non-zero instead of
   hanging.
2. **tiff-tail reads it live.** `keep_up` and `no_silent_subsample` PASS:
   `frames_read == frames_localized == n_frames`, `partial=false`, no dropped
   batches, bounded queue never overflowed.
3. **Live metrics are real on real signal.** `live_metrics_real` PASS with a
   non-None `nena_px`/`nena_nm` and positive `spots_per_frame` (needs the laser
   ON and a sample with signal — pick a FOV that blinks).
4. **The laser interlock fires on hardware.** `laser_interlock` PASS for all
   three exits (normal / abort / injected exception): after each, every laser is
   disabled and the shutter closed — via the public `IlluminationSystem.all_off()`
   (NOT a direct `.instrument` reach, which AttributeError'd on the first real
   run). Watch the lasers physically go dark.
5. **pycromanager-1.0 items.** `pycromanager_1_0` PASS: NDTiff-v3/ndstorage tail
   worked, the RAM-peek probe returned frames, and numpy-2 + pycromanager-1.0 +
   picasso coexist with no ABI crash. Check the recorded `version_block`
   (`mm_version`, `ndtiff`, `ndstorage`, `numpy`, `pycromanager`).
6. **Archive move.** If `--archive-dir` is on a real network share, confirm
   `archive` PASS with `verified=true` and the local copy removed only after the
   checksum matched.
7. **Coverage reconciles end-to-end** and the per-FOV record reached the
   registry (`registry_record` PASS) — with `--registry-url` if you want it in
   the real DB.

Then commit the `instrument_*.json` verdict and push for analysis.
