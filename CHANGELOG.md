# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **PycroFlow is now a picasso consumer** (WP-4 live localization). Added base
  dependencies: `picassosr` (temporarily **pinned to a git commit** carrying
  `localize_frames`, PR#705 — a TEMP bridge until picassosr 0.11.3 hits PyPI,
  then revert to `picassosr>=0.11.3`; tracked as planning Open-Decisions **B7**;
  this is the one flagged non-wheel git-URL dependency), `picasso-registry` (the
  per-FOV record sink client), and `python-ulid` (sortable `run_id`, matching the
  registry's ULID convention).
- **Stack-wide dependency harmonization (decision C41, picasso is the anchor).**
  Adopted picasso 0.11.3's shared-lib ranges. Base: `numpy>=1.24,<2` →
  `numpy>=2.2.6,<3` (no longer capped for pycromanager — the `[hardware]` stack
  is bumped to numpy-2-compatible pycromanager 1.0), `pandas>=2.3` →
  `pandas>=2.3.3,<3`, `pyyaml>=6.0` → `pyyaml>=6.0.3,<7`. `[hardware]`:
  `matplotlib>=3.10` → `matplotlib>=3.10.7,<4`. `[gui]`: `PyQt6` →
  `PyQt6>=6.10.2,<7`.
- **`[hardware]`: `pycromanager==0.29.5` → `pycromanager>=1.0,<2`** (numpy-2
  harmonization, C41). This is a MAJOR bump: the acquisition-code migration,
  Micro-Manager compatibility, and on-instrument validation are tracked as **B8
  / Gate-2** and are **not** done here (the acquisition code is not exercised in
  dev/CI). Dropped the explicit `ndtiff==2.2.1` pin — pycromanager 1.0 pulls its
  NDTiff reader (`ndstorage`) transitively, and the WP-1 reader already resolves
  `ndstorage` → `ndtiff` → `pycromanager` in turn.

### Fixed

- **WP-4 NeNA oracle now a real check** (numpy 2 unmasked it). `postprocess.nena`
  reads `Pixelsize` from its `info` argument, so passing `None` raises
  `ValueError: info must be a dict or a list of dicts`. The live
  `RunningMetrics` NeNA path was calling `nena(locs, None)` and swallowing that
  error → silently returning `None`; the T2 oracle then passed vacuously (both
  live and batch NeNA were `None`). `RunningMetrics` now carries the picasso
  `info` (`set_info`, wired from the frame source's `camera_info()` in
  `LiveAnalysisService`) and hands it to every `nena` call, so live NeNA is a
  genuine value; the oracle asserts live NeNA == batch NeNA over the same frames.

- Versioning now derives from the git tag via `setuptools-scm` (writes
  `PycroFlow/_version.py`); the manual `version` string in `pyproject.toml`
  is gone. `PycroFlow.__version__` reads the generated module with a fallback.
- Consolidated lint config into `pyproject.toml`: added `[tool.black]`
  (line-length 79, `target-version = ["py310"]`) and `[tool.flake8]`
  (`extend-ignore = E203,E501,W503` — Black owns line length), replacing the
  standalone `.flake8`.
- CI runner strategy (S0A-3): required checks now run on GitHub-hosted runners.
  Split the old combined `tests.yml` into hosted `lint.yml` + hosted
  `unit-tests-hosted.yml` (both trigger on push/PR to `master`/`develop`), and
  demoted the Windows unit tier to `run-unittests-windows.yml` triggered by
  `workflow_dispatch` only so a runner-less self-hosted/Windows check can't
  block merges. Branch protection should list only the hosted checks as
  required.

### Fixed

- `numpy`, `pandas`, and `openpyxl` moved from the `[hardware]` extra into the
  base `dependencies` — they are imported at module load by the core,
  hardware-free `fluid/legacy.py` (numpy) and `imaging.py` (pandas DataFrame +
  `to_excel`, which needs openpyxl), so a plain `pip install -e .` previously
  produced a package whose fluid/imaging modules (and their unit tests) could
  not import. This unblocks the hosted `Unit Tests (hosted)` job, which
  installs only `.[dev,gui]` (no `[hardware]` SDKs). All three are wheel-only,
  so the base install stays wheel-only.

### Added

- **WP-4 live analysis** (`PycroFlow/live_analysis/`): the first end-to-end
  slice — acquire one FOV → live-localize (no drift) → metrics to the Quality
  tab → one record to `picasso-registry`. A frontend-agnostic, headless
  `LiveAnalysisService` owns the acquisition + a `frames → locs` pipeline and
  pushes state over a thin client seam (`client_seam.py`: an update channel +
  early-abort control call; in-process Qt signals today, shaped so a
  WebSocket/SSE + REST client is a later drop-in). It never blocks acquisition:
  a **frame-source abstraction** (`frame_source.py`) reads frames — default
  `TiffTailFrameSource` (lossless, reuses WP-1's picasso `TiffMultiMap` reader,
  designed to run in a separate OS process; handles MM's ~4 GB `_1/_2` rollover
  via natural sort and per-`_Pos<N>` batching so a batch never spans two
  positions), plus a lossy `RamPeekFrameSource` (pycromanager
  `Core.get_last_tagged_image`, live-view only) and a hermetic `MockFrameSource`.
  A **pool of worker processes** (`compute_backend.py` / `worker.py`) runs
  picasso `localize_frames` off a **bounded queue**; when it can't keep pace it
  **lags** in contiguous batches (never subsamples the authoritative stream) —
  the runtime backpressure invariant (queue never exceeds its bound) is asserted
  in production code. Running metrics (`metrics.py`): NeNA, localizations/frame,
  background (no live drift correction). A ULID `run_id` is minted at experiment
  start (`run_id.py`) and tags every FOV/record. The compute backend + movie
  write-target are pluggable: `local-subprocess` reading local disk **ships now**
  (default); `remote-worker` (a LAN GPU node reading the pool folder) is a
  **stub** behind the same interface, gated on decision **B5** (register C33
  topology) so the switch is config, not a rewrite.
- **WP-4 laser fail-safe interlock** (`live_analysis/laser_interlock.py`, T3 /
  decision C21): the early-abort path AND any error/crash exit go through a
  `try/finally` interlock that disables every laser (monet's per-laser `enabled`
  setter, via `IlluminationSystem`) and closes the shutter, with automation
  defaulting `lasers_off_finally` ON. Fail-safe (one laser failing still disables
  the rest and closes the shutter) and never blocks/raises into acquisition.
- **WP-4 archive step** (`live_analysis/archive.py`): on the fallback path the
  raw movie is MOVED local → network-drive archive after the live read, with a
  verified sha256 checksum; the local copy is deleted ONLY after the archived
  copy is confirmed. Skipped when acquiring straight to the pool. Only
  localizations ever go to the cluster — the movie is never shipped there.
- **WP-4 Quality tab** (`PycroFlow/gui/tabs/quality_tab.py`): the first client
  over the streaming seam — live metrics (NeNA/locs-per-frame/background), a
  NeNA trend, live pipeline lag (bounded-queue depth), a thumbnail slot, and an
  early-abort button issuing the service's control call. Minimal (the full V0.8
  UI is WP-GUI); Qt is lazy-imported and runs headless.
- **WP-1 live-reader performance harness** (`PycroFlow/perf/`): a
  non-interactive, config-driven harness that benchmarks acquisition at the
  target frame rate with and without a concurrent incremental NDTiff reader,
  sweeping live-evaluation batch sizes and recording circular-buffer occupancy
  (time series + peak), dropped-frame count, and write throughput. Two frame
  sources selectable by flag — `--emulator` (pure-stdlib producer/consumer
  simulation, the CI dry run) and `--instrument` (real pycromanager
  acquisition + NDTiff `Dataset` read) — with identical measurement code. Run
  it via `python -m PycroFlow.perf` / `pycroflow-perf`; each invocation writes
  a timestamped run dir (`run_meta.json` + `metrics.csv` +
  `buffer_timeseries.csv`, schema pinned by `PycroFlow/perf/schema.py`). The
  circular buffer is configured in **MB** (matching Micro-Manager's sequence
  buffer). The large raw NDTiff acquisition is written to a separate
  `data_dir` on a data drive (required in instrument mode, never the repo) and
  deleted after each configuration is measured; only the small run dir is
  git-committed.
- **WP-1 separate-process reader** (`PycroFlow/perf/reader_process.py` /
  `pycroflow-perf-reader`): the design-intended live reader runs as a separate
  OS process reading the movie off disk, selectable with `reader_mode:
  process` (default) vs `thread` (`--reader-mode`). Isolates cross-process disk
  I/O contention from the acquisition's GIL / ZMQ bridge — the decisive
  go/no-go test for option (b). By default it reads through **picasso**
  (`picasso.io.TiffMultiMap`), the lab's analysis package and its actual reader
  for Micro-Manager NDTiff / OME-TIFF movies: tifffile builds a per-frame
  byte-offset table by walking the TIFF IFDs once, each frame is a pure
  `seek` + `readinto` from its offset, the multi-file `_NDTiffStack_N.tif` split
  is handled, and a partially-written trailing IFD is dropped — so it reads a
  still-growing file efficiently and safely, and generates exactly the read-load
  a real live analysis would. New frames are picked up by re-opening (a fast
  tifffile IFD scan), throttled to `reader_reopen_interval_s` (default 2 s) and
  only when the files have grown. If picasso is not installed (or fails), it
  falls back to reading via ndtiff's `Dataset` — resolving the class across SDK
  versions (`ndstorage` / `ndtiff` / top-level `pycromanager`) and reading by
  the dataset's own frame-axis name (pycromanager calls it `time`, not `t`),
  opening once and re-opening only rarely because re-opening a still-being-
  written NDTiff makes ndtiff rebuild its index by scanning every TIFF IFD
  (O(dataset size)). On stop the harness waits up to 300 s (was 60 s) for the
  reader's final flush, which on slow / network storage re-scans the whole movie
  and can take a minute or two — so `reader_frames_read` is not cut short. The
  harness now writes results **incrementally
  after every configuration** (append `metrics.csv` / `buffer_timeseries.csv`,
  refresh `run_meta.json` with `status` / `completed_configs` / `errors`), so a
  later acquisition failure never discards earlier results. The raw NDTiff for
  each configuration is now freed (and the separate reader torn down)
  immediately after that acquisition via `try`/`finally` — so a *failed*
  acquisition can no longer leak tens of GB and break the next config on a
  small local data drive — and `data_dir` defaults to a local path (local disk
  avoids the over-the-network write penalty; peak use stays one acquisition).
  Deletion now closes the NDTiff `Dataset` handle first and verifies the files
  are actually gone (retrying briefly), instead of `rmtree(ignore_errors=True)`
  which silently left memory-mapped NDTiff files on Windows while reporting
  success; a leftover is now reported as a `[wp1] WARNING`. A frame source that
  dies mid-acquisition (e.g. the disk fills) is surfaced as an error that stops
  the sweep and frees its partial data, rather than hanging.
- **WP-1 analysis** (`PycroFlow/perf/analyze_perf.py` / `pycroflow-perf-analyze`):
  ingests one or more run dirs and drafts the live-vs-batch go/no-go against
  documented thresholds, emitting `report.md` + `report.json` (+ optional
  matplotlib plots).
- Docs: `docs/WP-1-RUNBOOK.md` (how to run on the acquisition PC and commit the
  result dir back) and `docs/WP-1-perf-schema.md` (output schema + go/no-go
  thresholds); `results/` for the committed run logs.
- Per-subsystem selection: an `enabled` flag on the fluid / img / illu
  sections of an experiment design lets a subsystem be deselected. The
  builder omits deselected subsystems from the compiled Run Sequence, prunes
  cross-subsystem `wait for signal` entries that targeted a dropped
  subsystem, and raises if nothing is selected; the orchestrator only wires
  hardware for subsystems present in the protocol.
- Shared `.pre-commit-config.yaml` (pre-commit-hooks + Black + flake8 via
  Flake8-pyproject), matching the rest of the DNA-PAINT stack.
- `black --check` and `flake8` lint job in CI.
- Hosted (`ubuntu-latest`) `Unit Tests (hosted)` CI job
  (`unit-tests-hosted.yml`) intended as the required merge gate alongside the
  hosted `Lint` job: installs Qt runtime libs, `pip install -e ".[dev,gui]"`
  (base install stays wheel-only; the hardware stack is mocked), and runs the
  unit suite with `QT_QPA_PLATFORM=offscreen` so the GUI tests run headlessly.
- This changelog.

### Removed

- Legacy `setup.py` shim (`pyproject.toml` is the canonical build config).
- Empty `CHANGELOG.txt` (superseded by this `CHANGELOG.md`).

## [0.1.0]

Initial tagged release. PycroFlow coordinates microscopy image acquisition,
Hamilton fluid handling, and monet illumination control for automated
DNA-PAINT experiments (Exchange-PAINT, MERPAINT, Z-PAINT, SPH-RESI), with a
CLI (`pycroflow`) and a PyQt6 GUI (`pycroflow-gui`) over a shared service layer.
