"""Instrument-specific configs shipped with PycroFlow.

This subpackage holds YAML descriptions of instrument hardware — valves,
pumps, reservoirs, tubing volumes — previously baked into
``hamilton_architecture.py`` as module-scope dicts.

Per-microscope setups (``setups/<name>.yaml``, loaded by :func:`load_setup`)
group hardware by **role**, each role naming the driver serving it:

* ``fluid`` — ``pumps`` (``driver: hamilton-psd``), ``multiplexer``
  (``driver: hamilton-mvp`` with ``valves``, or ``driver: ibidi-multiflow``
  with its own serial ``port``), ``flush_pos``, ``reservoirs``, ``tubing``
* ``imaging`` — ``pfs_pars``
* ``illumination`` — ``{backend: monet, config: <monet.CONFIGS key>}``; the
  monet config names the *microscope*, which may differ from the setup's own
  name (see :func:`monet_config` and ADR 008)

:class:`LegacyArchitecture` still consumes a flat, vendor-shaped config dict;
:func:`assemble_hamilton_config` is the single translation point.

The package data is included via ``[tool.setuptools.package-data]`` in
``pyproject.toml``.
"""

import copy
from pathlib import Path

import yaml
from loguru import logger

_CONFIG_DIR = Path(__file__).resolve().parent
_SETUP_DIR = _CONFIG_DIR / "setups"

#: Multiplexer drivers a setup's ``fluid.multiplexer.driver`` may name.
HAMILTON_MVP = 'hamilton-mvp'
IBIDI_MULTIFLOW = 'ibidi-multiflow'
MULTIPLEXER_DRIVERS = (HAMILTON_MVP, IBIDI_MULTIFLOW)


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
    or a path to a YAML file. The result is normalised to the role-based
    layout (``fluid`` / ``imaging`` / ``illumination``, see
    :func:`_normalize_setup`) and ``fluid.tubing`` is converted to the
    tuple-keyed dict shape used by :class:`LegacyArchitecture`.

    Parameters
    ----------
    name : str
        Setup name or path.

    Returns
    -------
    dict
        The normalised setup.
    """
    path = _resolve(name, ".yaml", base=_SETUP_DIR)
    with open(path) as f:
        setup = yaml.safe_load(f)
    return _normalize_setup(setup, source=path)


def _normalize_setup(setup, source=None):
    """Return ``setup`` in the canonical role-based layout.

    Setups group hardware by *role* — ``fluid`` (pumps, multiplexer,
    reservoirs, tubing), ``imaging``, ``illumination`` — with each role naming
    the driver that serves it. The historical layout grouped by *vendor*
    (a single ``hamilton:`` block that also held the ibidi multiplexer, plus a
    top-level ``tubing:``); it is translated here so old setup files keep
    working, with a deprecation warning.

    Parameters
    ----------
    setup : dict
        A parsed setup YAML in either layout.
    source : path-like, optional
        Where it was read from — only used in the deprecation message.

    Returns
    -------
    dict
        The setup with a ``fluid`` block and an ``illumination`` block;
        ``fluid['tubing']`` is a tuple-keyed dict.
    """
    setup = copy.deepcopy(setup)
    if 'hamilton' in setup:
        setup = _translate_legacy_setup(setup, source=source)

    fluid = setup.setdefault('fluid', {})
    if isinstance(fluid.get('tubing'), list):
        fluid['tubing'] = _records_to_tubing(fluid['tubing'])

    illu = setup.setdefault('illumination', {})
    illu.setdefault('backend', 'monet')
    # Before illumination had its own block the setup's own name doubled as
    # the monet config key. Keep that as the fallback.
    if not illu.get('config'):
        illu['config'] = setup.get('setup')
    return setup


def _translate_legacy_setup(setup, source=None):
    """Translate a vendor-grouped (``hamilton:``) setup to the role layout."""
    logger.warning(
        "setup {} uses the deprecated vendor-grouped 'hamilton:' layout; "
        "please migrate it to the role-based 'fluid:' layout "
        "(see configs/setups/Mercury.yaml)".format(
            source if source is not None else setup.get('setup')))
    hamilton = setup.pop('hamilton')
    fluid = {'system_type': hamilton.get('system_type', 'legacy')}

    pumps = {'driver': 'hamilton-psd', 'interface': hamilton['interface']}
    for key in ('pump_a', 'pump_out'):
        if key in hamilton:
            pumps[key] = hamilton[key]
    fluid['pumps'] = pumps

    if hamilton.get('ibidi'):
        mux = dict(hamilton['ibidi'])
        mux['driver'] = IBIDI_MULTIFLOW
    else:
        mux = {'driver': HAMILTON_MVP,
               'valves': hamilton.get('valve_a', [])}
    fluid['multiplexer'] = mux

    for src, dst in (('flush_pos', 'flush_pos'),
                     ('valve_flush', 'valve_flush'),
                     ('reservoir_a_manifold', 'reservoirs')):
        if src in hamilton:
            fluid[dst] = hamilton[src]
    if 'tubing' in setup:
        fluid['tubing'] = setup.pop('tubing')

    setup['fluid'] = fluid
    return setup


def monet_config(setup):
    """Return the monet ``CONFIGS`` key a setup's illumination uses.

    The monet config names the *microscope* whose lasers are driven; a setup
    name may differ from it (e.g. the ``Ibidi`` fluidics setup running on the
    ``Mercury`` microscope). Falls back to the setup's own name for setups
    that predate the ``illumination`` block.

    Parameters
    ----------
    setup : dict or None
        A setup from :func:`load_setup`.

    Returns
    -------
    str or None
        The monet config name, or None if none is configured.
    """
    if not setup:
        return None
    illu = setup.get('illumination') or {}
    return illu.get('config') or setup.get('setup')


def setup_reservoirs(setup):
    """Return a setup's reservoir manifold entries (empty list if none)."""
    if not setup:
        return []
    return (setup.get('fluid') or {}).get('reservoirs', []) or []


def _flatten_fluid_config(fluid):
    """Flatten a role-based ``fluid`` block into a legacy hamilton config.

    :class:`LegacyArchitecture` still consumes the flat, vendor-shaped dict
    (``interface`` / ``valve_a`` / ``ibidi`` / ``pump_a`` / ...); the setup
    files describe roles. This is the single translation point between them.
    """
    pumps = fluid.get('pumps', {})
    config = {
        'system_type': fluid.get('system_type', 'legacy'),
        'interface': pumps['interface'],
    }
    for key in ('pump_a', 'pump_out'):
        if key in pumps:
            config[key] = pumps[key]
    for key in ('flush_pos', 'valve_flush'):
        if key in fluid:
            config[key] = fluid[key]

    mux = dict(fluid.get('multiplexer') or {})
    driver = mux.pop('driver', HAMILTON_MVP)
    if driver == HAMILTON_MVP:
        config['valve_a'] = mux.pop('valves', [])
    elif driver == IBIDI_MULTIFLOW:
        # No Hamilton rotary valves: the ibidi unit multiplexes. Its remaining
        # keys (port/baud/channels/address) configure IbidiMultiplexer.
        config['valve_a'] = []
        config['ibidi'] = mux
    else:
        raise ValueError(
            "unknown fluid.multiplexer.driver {!r}; expected one of "
            "{}".format(driver, list(MULTIPLEXER_DRIVERS)))
    return config


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
        A setup config from :func:`load_setup` (with a ``fluid`` block).
    fluid_settings : dict
        The experiment design's ``fluid.settings`` (needs ``reservoir_names``;
        optional ``special_names`` / ``cleaning_reservoirs``).

    Returns
    -------
    (dict, dict)
        ``(hamilton_config, tubing_config)`` ready for
        ``LegacyArchitecture(hamilton_config, tubing_config)``.
    """
    fluid = copy.deepcopy(setup['fluid'])
    hamilton = _flatten_fluid_config(fluid)
    manifold = fluid.get('reservoirs', []) or []
    by_id = {entry['id']: entry for entry in manifold}

    special_names = dict(fluid_settings.get("special_names", {}))
    cleaning = fluid_settings.get("cleaning_reservoirs", []) or []

    used_ids = list(fluid_settings.get('reservoir_names', {}).keys())
    # Reservoirs named only in special_names (e.g. flushbuffer_a) are routed
    # to just like any other — _flush() and fill_tubings() call
    # _set_valves(special_names['flushbuffer_a']) — so they must be wired in
    # too, even when the design does not also list them in reservoir_names.
    for rid in special_names.values():
        if rid not in used_ids:
            used_ids.append(rid)
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
                "Reservoir id {!r} is not wired in setup {!r}'s "
                "fluid.reservoirs (which wires {})".format(
                    rid, setup.get('setup'), sorted(by_id)))
        reservoir_a.append(by_id[rid])

    hamilton['reservoir_a'] = reservoir_a
    hamilton['special_names'] = special_names
    hamilton['cleaning_reservoirs'] = cleaning
    return hamilton, fluid.get('tubing', {})


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
