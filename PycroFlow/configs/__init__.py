"""Instrument-specific configs shipped with PycroFlow.

This subpackage holds YAML descriptions of fluid-handler hardware
topologies — valves, pumps, reservoirs, tubing volumes — previously baked
into ``hamilton_architecture.py`` as module-scope dicts. The loader
:func:`load_legacy_config` reads them and exposes the resulting dicts.

The package data is included via ``[tool.setuptools.package-data]`` in
``pyproject.toml``.
"""
from pathlib import Path

import yaml


_CONFIG_DIR = Path(__file__).resolve().parent


def _resolve(path_or_name, suffix):
    """Accept either a bare name ('default') or a path; return a Path."""
    p = Path(path_or_name)
    if p.suffix == '':
        p = _CONFIG_DIR / f'{path_or_name}{suffix}'
    return p


def load_legacy_system(name='legacy_system'):
    """Load a legacy system config YAML and return the parsed dict.

    ``name`` may be either a basename (e.g. ``'legacy_system'``) found in
    :mod:`PycroFlow.configs`, or an absolute / relative path to a YAML file.
    """
    path = _resolve(name, '.yaml')
    with open(path) as f:
        return yaml.safe_load(f)


def load_legacy_tubing(name='legacy_tubing'):
    """Load a legacy tubing config and convert list-of-records to
    tuple-keyed dict, matching the original in-source dict shape.

    YAML cannot natively represent tuple keys, so the on-disk format is::

        - from: R21
          to: pump_a
          volume: 365

    which round-trips to ``{('R21', 'pump_a'): 365, ...}``.
    """
    path = _resolve(name, '.yaml')
    with open(path) as f:
        records = yaml.safe_load(f)
    result = {}
    for record in records:
        result[(record['from'], record['to'])] = record['volume']
    return result
