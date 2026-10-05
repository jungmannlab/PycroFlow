# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **WP-LIVE-INT — live analysis on the orchestrated product path.** A normal
  `pycroflow`/`pycroflow-gui` run now gets the WP-4 live pipeline without any
  standalone launcher: `ExperimentService` owns a `LiveRunCoordinator`
  (`services/live_run.py`) that lazily builds the `LiveAnalysisService` at run
  start, mints the ULID run_id of record, runs one `run_fov` per imaging
  `acquire` step, and tears down (with the C21 end-of-run laser interlock) on
  finish/abort. Frames come from the production acquisition via a new
  `FrameTap` (`live_analysis/frame_tap.py`): `ImagingSystem.record_movie`
  brackets each FOV and `image_process_fn` tees every frame into an
  `ImageQueueFrameSource` (non-blocking; the hot path only pays a queue put).
  Enabled when the setup's imaging config carries a picasso `camera_info`
  block (tunables via a `live_analysis` block); `PYCROFLOW_LIVE_ANALYSIS=0`
  is the kill switch. The `EmulatedImagingSystem` synthesizes frames through
  the same tap, so the Emulator setup exercises the whole path.
- **"Live" tab in the main GUI**: the WP-GUI `LiveShell` is mounted in
  `PycroFlowMainWindow`, passive until a run starts, auto-connected to the
  run's live service and unsubscribed after. Its sidebar **Early-abort** ends
  only the CURRENT FOV's acquisition (frames so far stay saved, honest
  partial-coverage record, immediate T3 interlock) and the protocol continues
  — distinct from the orchestrator Abort in the Run Sequence tab.
- **Registry auto-connect**: a shared from-env client factory
  (`services/registry.py`, `PAINT_REGISTRY_URL` / `PAINT_REGISTRY_TOKEN` /
  `PYCROFLOW_REGISTRY_BUFFER` → WP-3 `BufferedRegistryClient`; unset = quietly
  disabled) now feeds the live service in production, and each run writes an
  **experiment-level record** (`build_experiment_payload` /
  `post_experiment_record`) that the per-FOV `acquisition_run` rows link to
  via `experiment_id`.

### Fixed

- `post_fov_record` now pre-mints row ids client-side, so the
  acquisition→fov→analysis→metrics FK chain works with the fire-and-forget
  `BufferedRegistryClient` (whose writes return an acknowledgement, not the
  row — chaining on the response id raised `KeyError` in production) and
  dedups exactly on at-least-once replay.
- `LiveAnalysisService.run_fov` records an early-abort honestly even when the
  frame source ends (sentinel) before the per-batch abort check runs again —
  previously such a FOV could be reported clean.
- **Review fixes (adversarial review of WP-LIVE-INT):**
  - The frame tap's live backlog is now **soft-bounded**
    (`live_analysis.max_pending_frames`, default 256): when the pipeline lags
    that far, frames are dropped from the live stream (counted, logged
    loudly) instead of accumulating raw frames in RAM without bound (OOM risk
    on long FOVs; the raw movie on disk is unaffected).
  - **No more blocking joins on hot threads:** FOVs are queued to a single
    per-run consumer thread (the acquisition thread only enqueues — the
    previous design joined the prior FOV's worker for up to 60 s inside
    `record_movie`), and `stop_run` hands joins/interlock/registry-close to a
    background teardown thread so the Abort button / `closeEvent` (GUI
    thread) never freeze on a draining pipeline (`wait_idle()` for tests).
  - **Abort requests are generation-counted**: `clear_abort(generation=...)`
    re-arms exactly the requests a finished FOV consumed, so an early-abort
    landing between FOVs is served to the next FOV instead of being silently
    lost; acquisition-side delivery is gated the same way (one request ends
    at most one MDA).
  - `record_movie` initialises `viewer` before the acquisition block — with
    `show_display: false` it previously raised `UnboundLocalError` after
    every movie (pre-existing).
  - The **C15 archive step is wired** on the orchestrated path:
    `record_movie` captures the finished dataset's on-disk path and, when the
    setup configures `live_analysis.archive_dir`, the per-FOV pipeline moves
    the raw movie; unset, the gap is logged once per run (no longer silent)
    and the record carries `raw_data_path`.
  - One `config['camera_info']` / `config['live_analysis']` spelling for real
    and emulated imaging systems (the `live_camera_info` attribute variant is
    gone); emulators feed the tap via the shared `FrameTap.feed_frames`
    bracketing helper instead of re-implementing the protocol; the
    record-surfacing triad is a single `surface_record` helper used by both
    the per-FOV and experiment-level posts.

## [0.2.0] - 2026-09-30

First deployable release of the live-analysis slice (C40 "release when useful").
The WP-4 `LiveAnalysisService` + WP-GUI operator frontend + the pycromanager-1.0
migration were validated together on the instrument — **Gate 2 passed all 7
checks on the acq PC** (frames 2000/2000, real NeNA, T3 interlock on all paths,
numpy-2 + pycromanager-1.0.2 + picasso coexist, archive). Depends on the B7
picasso git-pin (until picassosr 0.11.3 hits PyPI) and `monet@v0.4.2` /
`picasso-registry@v0.1.0` (neither on PyPI).

### Added

- **Gate-2 on-instrument integration harness** (`PycroFlow/perf/gate2_harness.py`):
  a single-command, non-interactive harness with `--mode emulator` (hermetic) /
  `--mode instrument` (real MDA + monet interlock) that writes a JSON verdict over
  7 checks (keep-up · no-silent-subsample · live-metrics · registry · T3 interlock
  · pycromanager-1.0 · archive). Full camera_info (`--baseline/--sensitivity/
  --gain/--qe`) + tunable `--min-net-gradient/--box-size`. **Passed on the acq PC
  2026-09-30.**
- **Shared `AcquisitionDriver`** (`live_analysis/acquisition_driver.py`): drives a
  real pycromanager MDA feeding the live pipeline via `image_process_fn` → queue;
  shared by the Gate-2 harness and the GUI `--live` mode.
- **WP-GUI: V0.8 look & feel** (`gui/live/theme.py`) — `setStyle("Fusion")` + the
  V0.8 dark/gold stylesheet, applied app-wide + on the shell root.
- **WP-GUI: `--demo` and `--live` launchers** (`python -m PycroFlow.gui.live`):
  `--demo` animates every panel via a real service + `MockFrameSource` (no
  instrument); `--live` runs a GUI-attached real acquisition (the Gate-2 clean-FOV
  path). Both feed the Overview and draw **optional picasso-style localization
  boxes** (`identify_in_image`, toggle checkbox).
- **WP-GUI: explicit "inert" marking** (`gui/live/inert.py`) — planned/not-yet-
  wired controls are dimmed+dashed+italic with a tooltip and an `inert` property;
  a pinned test forces un-flagging when a control is wired.

### Fixed

- **Live NeNA silently always `None`** — `RunningMetrics` passed `None` as picasso
  `info` to `nena()` and swallowed the error; now wires `camera_info` → `info`.
- **Gate-2 instrument: `frames_localized=0`** — the frame source handed picasso
  only `Pixelsize`; the fit needs the full photon-conversion keys
  (Baseline/Sensitivity/Gain/Qe). Fixed; interlock-path FOVs also switched to the
  driver-fed image queue (were on tiff-tail → hung).

### Added

- **`IlluminationSystem.all_off()` — public T3 fail-safe primitive**
  (`PycroFlow/illumination.py`): runs the lazy monet init (`_ensure_monet`)
  first, then disables **every** laser in `instrument.lasers` fail-safe
  (one laser failing still disables the rest) and closes the shutter, returning
  a `{disabled, failed, shutter_closed, errors}` report and never raising. The
  T3 laser interlock (`live_analysis/laser_interlock.py`) now **prefers this
  public method** over reaching into `.instrument` directly — the direct reach
  bypassed the lazy build and `AttributeError`'d on the first real acquisition
  (laser never disabled; interlock reported "all paths unsafe"). It falls back
  to the duck-typed `.instrument`/`set_laser_enabled` surface only for pure stub
  fakes. Gate-2 harness laser-enable now uses the public API + a `--laser` CLI
  arg (skips with a warning if omitted) instead of `illu.instrument.curr_laser`.

- **WP-GUI — composable operator frontend** (`PycroFlow.gui.live`): the
  V0.8-modelled operator UI rebuilt as a **composable shell** (C31 layout + C32
  shell + A13/C23 thin client) over WP-4's live-analysis seam. A **thin
  subscribing client** (`LiveClientBridge`) turns
  `live_analysis.client_seam.LiveUpdate`s into Qt signals and holds the
  early-abort control call — the single transport-swap point, shaped so a
  WebSocket/SSE+REST client is a later drop-in (no remote transport built now).
  The shell mounts per-module **contributions** discovered via a declared panel
  registry (`contribution.py`); the first contributor is the **operator module**
  (four tab groups: Setup · Live QC · Analysis · Assistant) plus the fixed
  **QC-at-a-glance sidebar** (~15 colour-coded metrics + advisor traffic-light +
  core controls) and the always-visible **Overview/Zoom** panel. **Multi-client
  capable**: several shells subscribe to one service hub independently. Advisor
  findings are consumed behind a **local adapter/Protocol** (`advisor.py`, fed by
  a `MockAdvisor` in tests) so the GUI does not hard-depend on picasso-workflow's
  `qc_advisor` — a marked TODO wires the real findings type via
  `FindingsAdapter.wrap` when WP-ADVISOR lands. Run standalone with
  `python -m PycroFlow.gui.live`. Fully **mock-stream testable**
  (`tests/test_live_gui.py`); headless-importable (offscreen Qt), no WP-4 core
  files modified.

### Changed

- **pycromanager 0.29 → 1.0 acquisition-code migration (WP-PYCRO-1.0, B8 /
  Gate-2; decision C41).** Completes the code-side migration that the C41 pin
  bump (`[hardware]` `pycromanager>=1.0,<2`) deferred. The acquisition API
  PycroFlow uses is largely stable across 0.29→1.0 **when running against the
  Micro-Manager Java backend** (MM 2.0 GUI + ZMQ bridge, which is PycroFlow's
  mode): the `Acquisition(...)` factory dispatches `show_display`,
  `image_process_fn`, `pre_hardware_hook_fn`, and the `image_process_fn(img,
  meta, event_queue)` / `event_queue.put(None)` abort contract through to the
  Java backend unchanged, and `get_dataset()` / `get_viewer()` remain. The one
  breaking change is the **`Dataset` relocation** — in 1.0 the NDTiff reader is
  `from ndstorage import Dataset`, no longer `from pycromanager import Dataset`;
  the WP-1 reader (`perf/reader_process.py`) already resolves `ndstorage →
  ndtiff → pycromanager` in turn, so it needed no change. WP-4's frame sources
  (`live_analysis/frame_source.py`) read via picasso `TiffMultiMap` (tail, the
  authoritative path) and `Core.get_last_tagged_image` (RAM peek) — neither
  touches the relocated `Dataset`, so both are unaffected. **Minimum
  Micro-Manager: a Micro-Manager 2.0 nightly build contemporaneous with (or
  newer than) pycromanager 1.0.0 (released 2024-08-28)** — the Python ZMQ
  client and the Java server bundled in the MM nightly share a version
  handshake, so an older MM nightly raises a version-mismatch. Verified
  statically/headlessly in-container against the real pycromanager 1.0.2; real
  MDA + numpy-2/acquisition ABI coexistence are on the on-instrument Gate-2
  checklist (this repo cannot run Micro-Manager).
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

- **`perf.InstrumentBackend.start()` validates `data_dir` before connecting to
  the MM Core** (WP-PYCRO-1.0). Previously it opened the Core connection first
  and only then checked `data_dir`, so the "refuse to write raw NDTiff into the
  repo" guard depended on Core being a fast-returning mock. With real
  pycromanager 1.0 installed, the connect blocks/times out before the guard
  runs. The guard now fails fast and deterministically regardless of backend.
- **WP-4 NeNA oracle now a real check** (numpy 2 unmasked it). `postprocess.nena`
  reads `Pixelsize` from its `info` argument, so passing `None` raises
  `ValueError: info must be a dict or a list of dicts`. The live
  `RunningMetrics` NeNA path was calling `nena(locs, None)` and swallowing that
  error → silently returning `None`; the T2 oracle then passed vacuously (both
  live and batch NeNA were `None`). `RunningMetrics` now carries the picasso
  `info` (`set_info`, wired from the frame source's `camera_info()` in
  `LiveAnalysisService`) and hands it to every `nena` call, so live NeNA is a
  genuine value; the oracle asserts live NeNA == batch NeNA over the same frames.
- **WP-4 adversarial-review fixes.**
  - *Acquisition integrity (no silent frame-loss):* `run_fov` now reconciles
    frames-read (emitted by the source) against frames-localized (folded into the
    metrics). A clean finish that dropped frames is logged loudly (not a bare
    `assert` stripped under `-O`); abort/error records the TRUE localized count
    plus an explicit `partial`/`aborted`/`errored` coverage block on the FOV
    record (acquisition status `live_localized_partial`, analysis status
    `aborted`/`errored`), so partial coverage is honest, never silent.
  - *NeNA docs corrected:* `nena`'s value is independent of `info` (info only
    prevents a raise on `None`); NeNA is over the full accumulated locs table.
    The oracle now runs the real throttled/incremental path and checks it equals
    an independent single-shot batch NeNA.
  - *Backpressure claim corrected:* the queue bound is enforced by
    `queue.Queue(maxsize)`, not a decorative bare `assert`; `stats()` now carries
    a real accounting check (`submitted == completed + queued + in-flight`, plus
    `unfinished`/`timed_out`).
  - *Drain-thread leak fixed:* the drain/feeder loops exit on the stop flag
    regardless of completion, so a hung/slow worker can't spin them forever;
    `drain_and_stop` always stops and joins, flagging `timed_out` with the
    leaked count. Shared counters are lock-guarded so `stats()` doesn't tear.
  - *Interlock safety:* an interlock that addressed zero lasers (empty/missing
    `lasers` mapping, no `curr_laser`) no longer reports `safe=True` — "safe"
    requires at least one laser actually disabled; the reason is recorded.
  - *Archive stale-dest wedge fixed:* copy to a per-process temp dest, verify it,
    then atomic-`os.replace` into place (clearing any stale partial), so a prior
    failed run can't wedge the checksum permanently.

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
