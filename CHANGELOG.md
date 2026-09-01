# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Versioning now derives from the git tag via `setuptools-scm` (writes
  `PycroFlow/_version.py`); the manual `version` string in `pyproject.toml`
  is gone. `PycroFlow.__version__` reads the generated module with a fallback.
- Consolidated lint config into `pyproject.toml`: added `[tool.black]`
  (line-length 79, `target-version = ["py310"]`) and `[tool.flake8]`
  (`extend-ignore = E203,E501,W503` — Black owns line length), replacing the
  standalone `.flake8`.

### Added

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
- Shared `.pre-commit-config.yaml` (pre-commit-hooks + Black + flake8 via
  Flake8-pyproject), matching the rest of the DNA-PAINT stack.
- `black --check` and `flake8` lint job in CI.
- This changelog.

### Removed

- Legacy `setup.py` shim (`pyproject.toml` is the canonical build config).
- Empty `CHANGELOG.txt` (superseded by this `CHANGELOG.md`).

## [0.1.0]

Initial tagged release. PycroFlow coordinates microscopy image acquisition,
Hamilton fluid handling, and monet illumination control for automated
DNA-PAINT experiments (Exchange-PAINT, MERPAINT, Z-PAINT, SPH-RESI), with a
CLI (`pycroflow`) and a PyQt6 GUI (`pycroflow-gui`) over a shared service layer.
