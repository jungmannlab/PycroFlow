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

> The harness sets Micro-Manager's circular-buffer footprint from `buffer_mb`
> in the config (the same value as Edit ▸ Hardware Configuration ▸ *Sequence
> buffer size*) and records the capacity MM actually reports.

### Disk & data location — important

The raw NDTiff movie is large: at the typical **100 ms / 10 Hz / 40000 frames /
1024×1024 / 16-bit** it is ≈ **80 GB per acquisition** (≈ 20 GB for the 512×512
centre quadrant). So:

- Point **`data_dir`** at a folder on a **local drive, NOT the repo drive and
  NOT a network/mapped drive** (e.g. `D:\pycroflow_wp1_rawdata`) — local disk
  avoids the over-the-network write penalty. Instrument mode refuses to run
  without it, so the movie never lands next to the repo.
- The harness **deletes each acquisition immediately after measuring it — even
  if that acquisition fails** (WP-1 needs only the metrics), printing
  `[wp1] deleted raw acquisition: …` as it does. So peak disk use is **one
  acquisition, not the whole sweep**: at 576×576 / 16-bit / 40000 frames that
  peak is ≈ 25 GB (`frame_bytes × n_frames`) — ensure the local drive has at
  least that free, or reduce `n_frames`. Pass `--keep-raw-data` (or
  `keep_raw_data: true`) only if you want to keep the movies.
- Only the small **run dir** (`run_meta.json` + two CSVs, kilobytes) is written
  under `output_dir` and git-committed. **The raw movie is never committed.**

---

## 1. Run the sweep — the one command

Edit `PycroFlow/perf/configs/wp1_default.yaml` so `frame_rate_hz`,
`exposure_ms`, `n_frames`, `buffer_mb`, and the image size / `roi` match your
target acquisition, then run (set `--data-dir` to your data drive):

```bat
python -m PycroFlow.perf --instrument ^
  --config PycroFlow\perf\configs\wp1_default.yaml ^
  --data-dir D:\pycroflow_wp1_rawdata
```

For the centre 512×512 quadrant, add `--roi 256,256,512,512` (or set `roi` in
the config).

### Reader mode — `process` (default) vs `thread`

`reader_mode` selects how the concurrent incremental reader runs:

- **`process` (default, the design-intended path)** — a **separate OS process**
  (`PycroFlow.perf.reader_process`) opens the movie while it is still being
  written and reads the already-written frames in contiguous batches, trailing
  behind acquisition. Because it is a distinct process reading files off disk
  (not a thread sharing the acquisition's GIL / pycromanager ZMQ bridge), it
  isolates whether concurrent reading *fundamentally* contends with the write
  path. **This is the decisive go/no-go test for live streaming (option b).**
  It reads through **picasso** (`picasso.io.TiffMultiMap`) when picasso is
  installed — the same reader picasso uses for real analysis, so the read-load
  is representative — and prints `[wp1-reader] using picasso TiffMultiMap
  reader`. If picasso is absent it falls back to ndtiff and prints
  `[wp1-reader] using ...Dataset`. Watch for the `opened movie in Xs` lines: on
  a very large full-frame movie each re-open still scans the growing TIFF IFDs,
  so raise `reader_reopen_interval_s` if opens are slow.
- **`thread`** — the reader runs in the acquisition process. This reproduces
  the earlier, more pessimistic same-process result; use it only for contrast
  (`--reader-mode thread`).

The reader trails acquisition by up to one batch (latency ≈
`batch_size / frame_rate`, e.g. batch 100 at 10 Hz ≈ 10 s behind). That lag is
on the analysis side only — reading off disk never sits in the acquisition's
save path, so it does not delay the camera/writing itself. On exit the reader
reports how many frames it read (recorded in `run_meta.json` under
`backend.reader_frames_read`) so you can confirm it kept up and read every
frame.

### Robustness — results are written after every configuration

The harness flushes results **after each acquisition**, not only at the end:
`metrics.csv` / `buffer_timeseries.csv` are appended and `run_meta.json` is
refreshed (with a `status` of `running` / `complete` / `error` and the list of
completed configurations) after every configuration. So if a *later*
acquisition fails, the configurations already measured are safely on disk — the
run dir is finalised with `status: "error"` and the error recorded, and you can
still commit and analyse the partial result.

That runs, in one process, at the target frame rate:

- a **baseline** acquisition with **no** concurrent reader, then
- one acquisition **per batch size** in the sweep, each with a concurrent
  incremental NDTiff reader pulling contiguous batches of that size.

It prints the run-dir path when done, e.g.:

```
Wrote run dir: results\wp1_instrument_20260826T141230Z
```

**Expected duration:** roughly `(1 + number_of_batch_sizes) × n_frames /
frame_rate_hz`, plus a few seconds of per-acquisition setup. With the defaults
(`n_frames: 2000`, `frame_rate_hz: 10`, four batch sizes) that is
≈ `5 × 200 s` ≈ **17 minutes**. A full-length `n_frames: 40000` run is
≈ `5 × 67 min` ≈ **5.5 hours** — use the default first, then scale up if you
want a full-length stress test. Reduce the batch-size list to shorten the
sweep.

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
