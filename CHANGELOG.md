# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
