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
  `buffer_timeseries.csv`, schema pinned by `PycroFlow/perf/schema.py`).
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
