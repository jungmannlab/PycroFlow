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

- Per-subsystem selection: an `enabled` flag on the fluid / img / illu
  sections of an experiment design lets a subsystem be deselected. The
  builder omits deselected subsystems from the compiled Run Sequence, prunes
  cross-subsystem `wait for signal` entries that targeted a dropped
  subsystem, and raises if nothing is selected; the orchestrator only wires
  hardware for subsystems present in the protocol.
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
