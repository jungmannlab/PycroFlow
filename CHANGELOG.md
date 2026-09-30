# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Fluidics monitoring **live view now actually starts** during a run. The GUI
  read the capture process's live-frame path once, at the moment the run locked
  the tab — but the capture process spawns a hair later, so the path was still
  `None` and the tab stayed on "Preview stopped". The tab now polls a resolver
  each tick, so the live view appears as soon as the stream is up.

### Changed

- Monitoring clips default to a **numbered** `<save_dir>/fluidics_cam[_N]` (first
  free suffix) instead of a fixed `fluidics_cam`, so multiple runs into one
  `save_dir` don't mix — mirroring the acquisition folder's `_N` numbering.

### Added

- Fluidics monitoring: **live view during a run**. Because the capture process
  owns the cameras while recording, it now also publishes its latest composite
  tile to a small file a few times a second; the GUI Webcams tab shows that as a
  live view for the whole acquisition (the editing controls stay locked). On by
  default; disable per rig with `monitoring.live: false`.

### Changed

- Progress bar counts **one round per exchange imager**: an imager's dark-frame
  acquisition is folded into its round instead of being counted as a separate
  round (Exchange-PAINT; the same dark-fold rule applies to any experiment type
  with dark acquisitions). Dark-free protocols are unaffected.
- Monitoring clip filenames now carry the **fluid step range** the exchange
  spans — `run_<id>_round<NNN>_fluid-step<a>-<b>_<UTC>.avi` (was a single
  `step<SSS>`); the registry row gains `protocol_step_end` alongside
  `protocol_step`.

### Added

- Fluidics monitoring: reliable real-webcam capture on Windows. The instrument
  source now selects the OpenCV backend (defaulting to **DirectShow** on
  Windows, since the default MSMF backend commonly opens a UVC webcam but only
  yields black frames), warms up a few frames on open, and logs the backend +
  resolution + whether a first frame arrived. Configurable via `monitoring.
  backend` (`dshow`/`msmf`/`v4l2`/`any`). New `pycroflow-capture --probe`
  scans camera indices × backends and reports which yield a live (non-black)
  frame, to find the right index/backend on a rig. See the runbook's black-
  preview section.
- Fluidics monitoring: clips now land **with the run** and carry their step.
  The `monitoring.output_dir` is optional — when omitted, clips default to
  `<experiment save_dir>/fluidics_cam/` (resolved at run start), so they sit
  beside the run's other outputs; an explicit `output_dir:` still overrides.
  Each clip's filename encodes the fluid run-sequence step the exchange started
  on (`run_<id>_round<NNN>_step<SSS>_<UTC>.avi`), and that `protocol_step` is
  written onto the `fluidics_round` registry row, so a clip maps to the exact
  Run Sequence entry.
- GUI **Webcams tab**: configure the whole monitoring `monitoring:` block
  without leaving the app — an **output-path** field (with Browse; blank = the
  experiment folder) and an editable **camera list** you can **add/remove**
  rows in (role, device index, resolution), plus a live low-fps tiled preview
  (emulator source for an emulated setup, real webcams otherwise; auto-stopped
  while a run owns the cameras). Apply (in-session, rebuilds the controller for
  the next run) and Save (writes the block back to the setup YAML).
- Fluidics monitoring webcams (WP-FLUIDICS-CAM, Phase 1): record one short
  movie per Exchange round of the fluid-exchange leg (the least-observable,
  most failure-prone part of a run — dry reservoir, mis-primed pump, bubble,
  leak). New `PycroFlow.monitoring` package: a camera-capture service
  (`pycroflow-capture`) that runs in its **own OS process** and, driven by the
  fluidics round lifecycle, writes one tiled `.avi` per round to a configured
  pool dir. Two frame sources record identically and differ only in the source:
  `--emulator` (synthetic numpy frames; the default, used by CI) and
  `--instrument` (real UVC/USB webcams via OpenCV, the optional `[monitoring]`
  extra). The clip writer is a wheel-only pure-Python uncompressed-AVI muxer, so
  the emulator path needs no camera and no camera library. Declare a rig's
  cameras with an optional `monitoring:` block in its setup YAML (see the new
  `EmulatorCam` setup and `docs/WP-FLUIDICS-CAM-RUNBOOK.md`); a setup with no
  such block leaves the subsystem inert. **Isolation invariant:** capture runs
  in a separate process behind a bounded, drop-oldest command queue, so a slow,
  saturated, or unplugged camera degrades to a logged gap (a black tile panel)
  and can never stall or perturb acquisition or the fluidics orchestration.
  Each clip's pool URI is best-effort indexed onto the matching
  `fluidics_round` registry record via `monitoring_video_uri` (the optional
  `[registry]` extra; a down/slow/absent registry never blocks the run). The
  Qt GUI auto-records per round when a camera setup is selected. Read-only, no
  actuation. `SignalRegistry` gained a best-effort observer hook and
  `ExperimentService` a symmetric `remove_state_observer`, both used by the
  monitoring controller without otherwise changing orchestration behaviour.

### Changed

- Run Sequence duration estimates are far more accurate. The per-step model
  (`protocols.timing.estimate_entry_duration`, which drives the design-tab ETA,
  the live remaining-time readout, and the `STEP_TIMING` log's `estimate_s`)
  only counted ideal fluid motion / exposure time and so badly underestimated
  every step — a run estimated at ~1 min actually took ~3.5 min. Calibrated
  from `STEP_TIMING` run logs, it now adds the dominant fixed overheads: an
  `inject` adds ~3.45 s (ibidi channel switching, Hamilton pump-valve
  rotations, the concurrently-driven extraction pump — so a 1 µl inject now
  reads ~3 s, not 0.01 s), a `pump_out` adds ~1.6 s, and an `acquire` adds
  ~0.09 s/frame of camera readout beyond exposure plus ~2 s of per-acquisition
  arm/PFS/ZMQ startup (100 frames @ 100 ms now ~21 s, not 10 s). Each overhead
  is overridable per setup via the subsystem `parameters` block
  (`est_inject_overhead` / `est_pumpout_overhead` / `est_frame_overhead` /
  `est_acquire_setup`) once `protocols.timing_analysis` calibrates it. Also
  removed a duplicated `STEP_TIMING_TAG` definition.
- Dropped the spurious 1 µl pump-out + 1 µl re-inject that preceded every
  flush when `vol_remove_before_flush` is 0 (its default). `create_step_pumpout`
  / `create_step_inject` floor volumes at 1 µl, so a 0 pre-removal compiled to a
  token 1 µl pump-out and a 1 µl re-inject per flush — time spent moving no
  meaningful liquid. `create_stepset_flush` now emits a single inject when no
  pre-removal is requested, and the full pump-out → inject → restore sequence
  only when `vol_remove_before_flush > 0`. Any real under-pressure/suction need
  is served by `inject_precreate_underpressure` (full-syringe pull) or a
  non-zero `vol_remove_before_flush`, not the 1 µl artefact. The `exchange_basic`
  regression snapshot was regenerated (10 × 1 µl injects and 10 × 1 µl
  pump-outs removed).
- Unified the imager/reagent injection volumes across experiment types and
  added an optional post-imaging top-up. `fluid.settings.vol_reagent` is now
  the volume dispensed into the sample **before** imaging each round (imager /
  adapter / blocker) for **both** Exchange and SPH-RESI, and the new
  `vol_reagent_post` is an optional volume dispensed **after** each acquisition
  (skipped when unset). The Exchange builder previously (mis)used
  `vol_imager_post` as its pre-imaging volume and ignored `vol_reagent`; it now
  reads `vol_reagent`, **falling back to `vol_imager_post`** so pre-split
  Exchange designs keep working unchanged. `vol_imager_post` is renamed to
  `vol_reagent_post` in the schema/editor. The regression fixture
  `exchange_basic` and the protocol/description tests moved to the new names
  (its snapshot was regenerated: the imager pre-inject is now the full
  `0.9·imager_volume` and a `0.1·imager_volume` post-inject was added). The
  initial-imager Exchange round injects no top-up (that imager is already in
  the sample and need not be a reservoir).

### Added

- The Fluid live schematic now also draws the **standard Hamilton MVP
  rotary-valve** topology (not just the ibidi multiplexer). Each chained valve
  is a hub drawn **on top**, with its reservoirs stacked in one or two short
  columns **below** it (rather than one wide row, to save horizontal space and
  keep every box readable); the hub's tubing drops radially to a per-column
  rail and branches into each box, and the hub notes its selected port. A
  rotary valve selects one port at a time, so the live path (hub → rail →
  box) is lit and a reservoir box goes green only when its whole root→leaf
  path is live (bridge ports that chain to the next valve are drawn
  hub-to-hub, and the root valve sits nearest pump_a). The boxes carry the
  same volume gauges and hover tooltip (which names the valve→port path) as
  the ibidi ports. Backed by
  `SystemService.fluid_topology()` (new `valves` block: per-valve `taps` /
  `bridges` + per-reservoir `(valve, port)` `routes`) and `fluid_state()` (new
  `valves` map of each MVP valve's last-selected position); the MVP `Valve`
  now caches its `valve_pos` in-process like the pump.
- The Fluid schematic now draws a **waste container** (beside the sample)
  with a live/expected fill gauge analogous to the reservoirs. It is the one
  physical waste bottle both pumps dispense into, fed by two legs: pump_out's
  extraction (lit while pump_out pushes ``out``) and, **only when the setup's
  tubing wires it** (``pump_a → flush_waste``), pump_a's flush. The gauge sums
  both sinks and fills bottom-up with the consumed fraction over ``used /
  total`` — the extraction total is derived from the protocol
  (``extractionfactor × volume`` summed) and accrues live as the run runs;
  flush volume accrues when ``fill_tubings`` flushes (its total backfilled
  from what it received). Backed by `SystemService.fluid_waste_labels()` (+ a
  `flush_waste` flag on `fluid_topology()`) and new `waste_totals` /
  `waste_used` tracking on the fluid handler. Previously waste was only a text
  label on the pump.
- The Fluid live view now tracks **per-reservoir volume**: each in-use port
  shows two vertical gauges — a blue "tank" on the left that starts full and
  drains as the reagent is pumped out, and a waste column on the right that
  fills upward as it is consumed — with exact figures (`X used / Y needed
  (Z%)`) in the hover tooltip. The fluid handler accumulates pumped volume per
  reservoir as inject steps run (`reservoir_used`) and derives the plan from
  the assigned protocol (`reservoir_totals`);
  `SystemService.fluid_reservoir_labels()` exposes both, read cache-only so the
  schematic stays live during a run without serial I/O. The main window also
  opens wide enough (1280×820) to show the wiring schematic without resizing.
- The Run Sequence progress readout now names the **current action** in plain
  language instead of the raw `$type`: e.g. `inject Imager 1`, `acquire EGFR`,
  `extract`, `wait`, `sync` (reservoir names come from the loaded design; a
  bare Run Sequence falls back to `reservoir <id>`). This joins the existing
  `Round k/N: <round name>` prefix (round names are the acquire steps' labels,
  e.g. `R1` / `EGFR barcode (pre)` / `A1 RESI round 2`), so the status line now
  reads e.g. `Round 2/5: EGFR   fluid 7/20 (inject Imager 1)   img 2/5 (acquire
  EGFR)`. Backed by `protocols.describe.action_label`.
- Experiment Design tab now previews **what a design will do**: the compiled
  sequence of events (e.g. "Pump 101 µl of Imager 1 into the sample → Acquire
  30000 frames → Pump 501 µl of Buffer …") and the **total reagent volumes**
  required (per reservoir + grand total, plus total waste — which counts the
  extraction pump's simultaneous `extractionfactor × volume` withdrawal on
  every inject, not just standalone pump-outs), in a foldable "Sequence &
  volumes" panel. The total reagent volume also rides alongside the
  live duration estimate. Backed by pure helpers `protocols.describe`
  (`describe_protocol`) and `protocols.timing` (`estimate_volumes` /
  `format_volume`), both read from the compiled Run Sequence so they work for
  every experiment type.
- Hover tooltips on the Experiment Design parameters explaining what each does
  (volumes, velocities, `extractionfactor`, wash buffers, imagers, laser
  power, …). The schema-driven form now shows the tooltip on the parameter
  **label**, not just the input, so hovering the name answers "what is this?".
- The fluid schematic now shows each reservoir's **name** (from the design) on
  its port and **dims reservoirs the loaded design does not use**, so it is
  clear at a glance which reservoirs are in play. Backed by
  `SystemService.fluid_reservoir_labels()`.

- Live fluid-wiring schematic in the GUI **Fluid** tab: a custom-painted panel
  that draws the ibidi multiplexer's 24 ports on their physical 6×4 grid
  (numbered left-to-right, bottom-to-top: port 1 lower-left wired to pump_a,
  port 7 above port 1), the meandered manifold tubing traced as edges from
  each reservoir's `valve_pos`, and pump_a / sample / pump_out.
  It overlays live state — open/closed channels, the energised flow path, each
  pump's valve position (IN → multiplexer / OUT → sample) and syringe fill —
  polling cached driver attributes (`multiplexer.channel_states`,
  `pump.valve_pos` / `target_volume`) every 300 ms, so it issues no serial
  traffic and stays live during a run. Hovering a port (or picking a reservoir
  in the manual "Set valves" dropdown) highlights that reservoir's full

- Live fluid-wiring schematic in the GUI **Fluid** tab: a custom-painted panel
  that draws the ibidi multiplexer's 24 ports on their physical 6×4 grid
  (numbered left-to-right, bottom-to-top: port 1 lower-left wired to pump_a,
  port 7 above port 1), the meandered manifold tubing traced as edges from
  each reservoir's `valve_pos`, and pump_a / sample / pump_out.
  It overlays live state — open/closed channels, the energised flow path, each
  pump's valve position (IN → multiplexer / OUT → sample) and syringe fill —
  polling cached driver attributes (`multiplexer.channel_states`,
  `pump.valve_pos` / `target_volume`) every 300 ms, so it issues no serial
  traffic and stays live during a run. Hovering a port (or picking a reservoir
  in the manual "Set valves" dropdown) highlights that reservoir's full
  expected path to the pump, so the intended route can be compared against the
  live open-valve path at a glance. Clicking a port toggles that ibidi
  channel open/closed and clicking a pump flips its syringe valve (in ↔ out)
  — both raw manual overrides that ignore reservoir routing, backed by
  `SystemService.toggle_multiplexer_channel()` / `toggle_pump_valve()`, run
  off the GUI thread and blocked while the orchestrator holds the run lock.
  The port-1→pump_a feed is drawn out to the left of the grid and over its
  top so it no longer crosses the other reservoir ports. Backed by new
  frontend-agnostic `SystemService.fluid_topology()` (incl. per-reservoir
  `routes`) / `fluid_state()`. Optional
  `fluid.multiplexer.grid_cols` / `pump_channel` keys tune the drawn geometry
  (default 6 / port 1). Removed a stale duplicate of the Fluid tab's
  `_refresh_reservoirs` / `_update_route_hint` while wiring this in.

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

- Manual "Pump move" in the Fluid tab now honours a `dispense_dir` of `out`
  even when a dispense reservoir is set. Routing to a reservoir (via
  `_set_valves`) also drives pump_a's valve to its input side ("in"), so a
  `dispense_res` supplied alongside `dispense_dir='out'` silently clobbered the
  requested `out` back to `in` — most visible on the ibidi setup, where the
  pump valve *is* one of the reservoir's routed valves (`valve_pos: {..., 1:
  in}`). `_pump` now applies `pickup_res` / `dispense_res` only when the
  matching direction is the input (reservoir) side, ignoring (and logging) the
  reservoir otherwise. All existing callers already pair a reservoir with an
  `in` direction, so their behaviour is unchanged.
- `fill_tubings` (and `fill_tubings_reverse` / cleaning) no longer raise
  `KeyError: "Cannot find any tubing configuation entry leading from R… to a
  valve"` on the ibidi MultiFlOW setup. The multiplexer is not part of the
  tubing graph, so reservoirs daisy-chain toward the pump (`R2 → R1 → pump_a`)
  rather than each tubing directly to a valve. `TubingConfig.
  get_reservoir_to_closest_valve` now falls back to the reservoir's own
  outgoing segment into the next reservoir junction — the dead leg to prime —
  when no valve/`pump_a` is directly downstream. A reservoir dead-ending at a
  non-reservoir sink (e.g. `sample`) still raises.
- `numpy`, `pandas`, and `openpyxl` moved from the `[hardware]` extra into the
  base `dependencies` — they are imported at module load by the core,
  hardware-free `fluid/legacy.py` (numpy) and `imaging.py` (pandas DataFrame +
  `to_excel`, which needs openpyxl), so a plain `pip install -e .` previously
  produced a package whose fluid/imaging modules (and their unit tests) could
  not import. This unblocks the hosted `Unit Tests (hosted)` job, which
  installs only `.[dev,gui]` (no `[hardware]` SDKs). All three are wheel-only,
  so the base install stays wheel-only.
- Exchange builder no longer crashes compiling a design that omits (or
  deselects) the `illu` block: `create_steps_exchange` read the illumination
  settings via `config.get("illu", {}).get("settings")`, which raised
  `AttributeError` when `illu` was present-but-`None`. Now uses
  `(config.get("illu") or {})`, matching the MERPAINT builder.
- `imaging.record_movie` no longer raises `UnboundLocalError` on `viewer` when
  an acquisition runs with `show_display` off — `viewer` is now bound to
  `None` before the acquisition block so the post-acquisition close check is
  safe.
- Reconnecting the Hamilton fluid bus no longer fails with the serial port
  "already occupied". `SerialBus.initialize` now releases any port it already
  holds before opening a new one (and `disconnect` drops the handle), so
  re-applying a changed design — or reconnecting after switching setups — works
  without restarting the GUI. A changed design's reservoirs are also applied
  live on **Translate** (via `update_reservoirs`, no serial reconnect needed).
- Removed a leftover `6 → 7` connector in the ibidi schematic: it came from
  reservoirs 19–23 whose `Ibidi.yaml` routes still listed `6, 7` adjacent (a
  meander-numbering remnant) rather than `6, 12, 7` like the others. The routes
  are reordered to the real tubing path (channel order does not affect
  routing), so the wiring tree is consistent.

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
