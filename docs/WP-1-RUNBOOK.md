# WP-1 — live-reader performance harness · RUNBOOK

**Audience:** you, on the Windows **acquisition PC** (there is no Claude Code
there). This is the "run there" leg of the WP-1 round-trip: Claude built and
validated the harness in the dev container on the PycroFlow emulator; you run
it on the instrument and commit the result logs back; Claude analyses them here
and drafts the go/no-go.

**What it decides:** whether reading the NDTiff dataset *incrementally, during
acquisition* (live streaming, option **b**) compromises acquisition — vs.
processing after each FOV (post-FOV batch). This is Gate 1.

The harness is **non-interactive**: one command per mode, no prompts,
config-driven, and it writes a single timestamped run directory. The *only*
difference between the emulator dry run and the instrument run is the frame
source — everything measured is identical.

---

## 0. One-time setup on the acquisition PC

```bat
:: from the PycroFlow repo root, in the lab environment
git fetch
git checkout feature/WP-1-perf-test
git pull

:: install with the hardware extra (pulls pycromanager + ndtiff)
pip install -e ".[hardware]"
```

Start **Micro-Manager** with the usual configuration and make sure the
pycromanager ZMQ server is enabled (Tools ▸ Options ▸ *Run server on port
4827*), exactly as for a normal PycroFlow acquisition. Close any standalone
monet GUI so the MM Core lock is free.

> Set the circular-buffer size in Micro-Manager (Edit ▸ Hardware
> Configuration ▸ *Sequence buffer size*) if you want it larger than the
> harness default; the harness records whatever capacity MM reports.

---

## 1. Run the sweep — the one command

Edit `PycroFlow/perf/configs/wp1_default.yaml` so `frame_rate_hz`,
`exposure_ms`, and `buffer_size` match your target acquisition, then run:

```bat
python -m PycroFlow.perf --instrument --config PycroFlow\perf\configs\wp1_default.yaml
```

That runs, in one process, at the target frame rate:

- a **baseline** acquisition with **no** concurrent reader, then
- one acquisition **per batch size** in the sweep, each with a concurrent
  incremental NDTiff reader pulling contiguous batches of that size.

It prints the run-dir path when done, e.g.:

```
Wrote run dir: results\wp1_instrument_20260826T141230Z
```

**Expected duration:** roughly `(1 + number_of_batch_sizes) × n_frames /
frame_rate_hz`, plus a few seconds of per-acquisition setup. With the default
`n_frames: 5000`, `frame_rate_hz: 100`, and five batch sizes that is
≈ `6 × 50 s` ≈ **5–6 minutes**. Scale `n_frames` up for a more stressful test.

Everything lands under `results/wp1_instrument_<UTC-timestamp>/`:

| file | contents |
|---|---|
| `run_meta.json` | host, OS, Python / PycroFlow / pycromanager versions, camera + MM version, mode, frame rate, buffer size, sweep list, git commit, UTC start/end, full config |
| `metrics.csv` | one row per (mode × configuration): occupancy peak/mean, dropped count/fraction, throughput |
| `buffer_timeseries.csv` | circular-buffer occupancy vs frame index |

The schema is documented in [`WP-1-perf-schema.md`](WP-1-perf-schema.md).

### Optional: a quick emulator dry run first

To confirm the branch runs end-to-end before spending instrument time
(identical command, emulated frame source, ~seconds):

```bat
python -m PycroFlow.perf --emulator --frames 2000
```

---

## 2. Commit the result dir back

The whole `results/…` directory is committed to the branch so Claude can
analyse it here:

```bat
git add results\wp1_instrument_20260826T141230Z
git commit -m "WP-1: instrument perf run (target rate, batch sweep)"
git push
```

Commit **one directory per run**. If you do several runs (e.g. different frame
rates or buffer sizes), commit each — Claude can analyse them together.

Tell Claude the run dir name(s) you pushed.

---

## 3. What Claude does back here (for reference)

```bash
python -m PycroFlow.perf.analyze_perf results/wp1_instrument_20260826T141230Z
```

This ingests the committed run dir(s), compares every with-reader
configuration against the no-reader baseline, and writes `report.md` +
`report.json` (+ plots) with the go/no-go against the documented thresholds
(dropped fraction, throughput retention, peak-occupancy fraction — see the
schema doc). Claude then drafts the go/no-go for your review.

- **GO** → adopt live incremental NDTiff reading (option b) as the primary
  live path.
- **NO-GO** → fall back to post-FOV batch (or the watchdog, option c) and
  investigate the contention.

---

## Troubleshooting

- **`MmLockHeld` / cannot attach to MM** — a monet GUI or another PycroFlow
  process holds the MM Core lock. Close it and retry.
- **`pycromanager` import / ZMQ errors** — Micro-Manager isn't running or the
  server port is off; start MM and enable the server (step 0).
- **Zero frames written / immediate finish** — check the camera device is
  selected in MM and the exposure/ROI in the config are valid for it.
- **Re-running** — each run makes a new timestamped dir, so runs never
  overwrite each other; the emulator and instrument dirs are named by mode.
