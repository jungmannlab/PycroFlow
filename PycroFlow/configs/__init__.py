"""Instrument-specific configs shipped with PycroFlow.

This subpackage holds YAML descriptions of fluid-handler hardware
topologies — valves, pumps, reservoirs, tubing volumes — previously baked
into ``hamilton_architecture.py`` as module-scope dicts. The loader
:func:`load_legacy_config` reads them and exposes the resulting dicts.

The package data is included via ``[tool.setuptools.package-data]`` in
``pyproject.toml``.
"""

import copy
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent
_SETUP_DIR = _CONFIG_DIR / "setups"


def _resolve(path_or_name, suffix, base=None):
    """Accept either a bare name ('default') or a path; return a Path."""
    p = Path(path_or_name)
    if p.suffix == "":
        p = (base or _CONFIG_DIR) / f"{path_or_name}{suffix}"
    return p


def _records_to_tubing(records):
    """Convert the on-disk tubing record list to a tuple-keyed dict.

    YAML cannot represent tuple keys, so tubing volumes are stored as
    ``{from, to, volume}`` records that round-trip to
    ``{('from', 'to'): volume, ...}``.
    """
    result = {}
    for record in records:
        result[(record["from"], record["to"])] = record["volume"]
    return result


def load_legacy_system(name="legacy_system"):
    """Load a legacy system config YAML and return the parsed dict.

    ``name`` may be either a basename (e.g. ``'legacy_system'``) found in
    :mod:`PycroFlow.configs`, or an absolute / relative path to a YAML file.
    """
    path = _resolve(name, ".yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def load_legacy_tubing(name="legacy_tubing"):
    """Load a legacy tubing config and convert list-of-records to
    tuple-keyed dict, matching the original in-source dict shape.

    YAML cannot natively represent tuple keys, so the on-disk format is::

        - from: R21
          to: pump_a
          volume: 365

    which round-trips to ``{('R21', 'pump_a'): 365, ...}``.
    """
    path = _resolve(name, ".yaml")
    with open(path) as f:
        records = yaml.safe_load(f)
    return _records_to_tubing(records)


# --- Per-microscope setup (hardware) configs -----------------------------


def list_setups():
    """Return the names of the available setup configs.

    Returns
    -------
    list of str
        Sorted base names of ``configs/setups/*.yaml`` (e.g.
        ``['Emulator', 'Mercury']``).
    """
    if not _SETUP_DIR.is_dir():
        return []
    return sorted(p.stem for p in _SETUP_DIR.glob("*.yaml"))


def load_setup(name):
    """Load a per-microscope setup (hardware) config.

    ``name`` is a setup basename in ``configs/setups`` (e.g. ``'Mercury'``)
    or a path to a YAML file. The ``tubing`` record list is converted to the
    tuple-keyed dict shape used by :class:`LegacyArchitecture`.

    Parameters
    ----------
    name : str
        Setup name or path.

    Returns
    -------
    dict
        The parsed setup with ``tubing`` converted to a tuple-keyed dict.
    """
    path = _resolve(name, ".yaml", base=_SETUP_DIR)
    with open(path) as f:
        setup = yaml.safe_load(f)
    if isinstance(setup.get("tubing"), list):
        setup["tubing"] = _records_to_tubing(setup["tubing"])
    return setup


def assemble_hamilton_config(setup, fluid_settings):
    """Combine a setup's hardware with an experiment's reservoir choices.

    The setup holds the full reservoir manifold wiring (all candidate
    positions); the experiment design selects which reservoirs are actually
    used (via ``reservoir_names``) and supplies ``special_names`` /
    ``cleaning_reservoirs``. This reproduces the merge previously hand-coded
    in ``start_experiment.py`` and returns a config shaped exactly like the
    ``hamilton_config`` that :class:`LegacyArchitecture` expects, plus the
    tubing dict.

    Parameters
    ----------
    setup : dict
        A setup config from :func:`load_setup` (with ``hamilton`` and
        ``tubing`` keys).
    fluid_settings : dict
        The experiment design's ``fluid.settings`` (needs ``reservoir_names``;
        optional ``special_names`` / ``cleaning_reservoirs``).

    Returns
    -------
    (dict, dict)
        ``(hamilton_config, tubing_config)`` ready for
        ``LegacyArchitecture(hamilton_config, tubing_config)``.
    """
    hamilton = copy.deepcopy(setup["hamilton"])
    manifold = hamilton.pop("reservoir_a_manifold", [])
    by_id = {entry["id"]: entry for entry in manifold}

    special_names = dict(fluid_settings.get("special_names", {}))
    cleaning = fluid_settings.get("cleaning_reservoirs", []) or []

    used_ids = list(fluid_settings.get("reservoir_names", {}).keys())
    for res in cleaning:
        if isinstance(res, int):
            rid = res
        else:
            rid = special_names.get(res)
            if rid is None:
                raise KeyError(
                    "Cleaning reservoir {!r} is neither an int id nor a "
                    "name in special_names {}".format(res, special_names)
                )
        if rid not in used_ids:
            used_ids.append(rid)

    reservoir_a = []
    for rid in used_ids:
        if rid not in by_id:
            raise KeyError(
                "Reservoir id {!r} is not wired in setup "
                "{!r}'s reservoir_a_manifold".format(rid, setup.get("setup"))
            )
        reservoir_a.append(by_id[rid])

    hamilton["reservoir_a"] = reservoir_a
    hamilton["special_names"] = special_names
    hamilton["cleaning_reservoirs"] = cleaning
    return hamilton, setup.get("tubing", {})


def assemble_imaging_config(setup, design):
    """Build the imaging config from a setup + an experiment design.

    The setup supplies the fixed ``pfs_pars`` (under ``imaging``); the design
    supplies ``save_dir`` / ``base_name`` (and optionally ``use_positions``).

    Parameters
    ----------
    setup : dict
        A setup config from :func:`load_setup`.
    design : dict
        An experiment design dict (``save_dir``, ``base_name``, ``img``).

    Returns
    -------
    dict
        Config for :class:`PycroFlow.imaging.ImagingSystem`.
    """
    cfg = copy.deepcopy(setup.get("imaging", {}))
    cfg.setdefault("save_dir", design.get("save_dir", "."))
    cfg["base_name"] = design.get("base_name", "experiment")
    img = design.get("img", {})
    settings = img.get("settings", {}) if isinstance(img, dict) else {}
    if "use_positions" in settings:
        cfg["use_positions"] = settings["use_positions"]
    return cfg
