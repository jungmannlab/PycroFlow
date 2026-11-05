"""Headless logic for the first-run setup wizard (no Qt).

The GUI wizard (:mod:`PycroFlow.gui.setup_wizard`) is a thin shell over this
module, so the whole flow — first-run detection, writing the per-machine
``.env``, enumerating serial ports, cloning a setup template, and the
cross-project connectivity probes — is importable and unit-testable without a
display or PyQt6.

Two things are persisted, mirroring monet's split:

* a gitignored, per-machine **``.env``** (0600) with the connectivity secrets +
  paths the frontends read at startup (``PAINT_REGISTRY_*``,
  ``MONET_CONFIG_PATHS``, ``PYCROFLOW_SPILL_PORT``); and
* a user-local **setup YAML** (via :func:`PycroFlow.configs.save_setup`),
  cloned from a shipped template and edited — the rig description that is no
  longer committed to the package.

The ``.env`` writer is dependency-free (``python-dotenv`` is not a base
dependency — it only *reads* ``.env`` when present), so the wizard works on a
bare install.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from loguru import logger

from PycroFlow import configs
from PycroFlow.envfile import repo_root_env_path

#: The connectivity variables the wizard writes to ``.env``.
ENV_REGISTRY_URL = "PAINT_REGISTRY_URL"
ENV_REGISTRY_TOKEN = "PAINT_REGISTRY_TOKEN"
ENV_MONET_CONFIG_PATHS = "MONET_CONFIG_PATHS"
ENV_MONET_TOKEN = "PAINT_MONET_TOKEN"
ENV_SPILL_PORT = "PYCROFLOW_SPILL_PORT"
#: Default microscope setup the GUI pre-selects on launch (the toolbar combo is
#: the microscope selector — there is no CLI arg). Written by the wizard.
ENV_DEFAULT_SETUP = "PYCROFLOW_SETUP"
#: Where user-local setups and acquisition data live (overridable per machine /
#: pointable at a lab-shared network folder). Written by the wizard.
ENV_SETUPS_DIR = "PYCROFLOW_SETUPS_DIR"
ENV_SAVE_DIR = "PYCROFLOW_SAVE_DIR"


def default_save_dir():
    """An accessible default acquisition-output directory (editable later).

    ``%USERPROFILE%\\Documents\\PycroFlow\\data`` on Windows, ``~/PycroFlow/data``
    elsewhere — a visible, user-writable place, NOT the hidden per-user app dir
    the frozen launcher chdirs to. The operator can repoint it (e.g. at a lab
    network drive) in the wizard.
    """
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    base = docs if os.path.isdir(docs) else home
    return os.path.join(base, "PycroFlow", "data")


def normalize_com_port(value):
    """Return the bare serial-port number a Hamilton ``interface.COM`` wants.

    The Hamilton PSD/MVP driver builds the OS port name by prefixing ``COM`` to
    this value, so it must be the number alone (``'3'``), not ``'COM3'``. The
    wizard's port picker shows pyserial device names (``COM3``) and operators
    type either form — strip a leading ``COM`` (case-insensitive) + whitespace
    so ``'COM3'`` / ``' com3 '`` / ``'3'`` all become ``'3'``. A non-COM value
    (e.g. a POSIX ``/dev/ttyUSB0`` in tests) is returned stripped, unchanged.
    """
    s = str(value).strip()
    if s[:3].upper() == "COM":
        return s[3:].strip()
    return s


#: Default template the wizard clones a new setup from (a shipped built-in).
DEFAULT_TEMPLATE = "Template"


def env_path() -> Path:
    """Return the ``.env`` path the wizard writes.

    ``<PYCROFLOW_HOME>/.env`` when the frozen-GUI launcher set
    ``PYCROFLOW_HOME`` (the onefile unpack dir is wiped on exit), else the
    repo-root ``.env`` next to ``.env.template`` (editable installs).
    """
    home = os.environ.get("PYCROFLOW_HOME")
    if home:
        return Path(home) / ".env"
    return Path(repo_root_env_path())


def first_run() -> bool:
    """Whether this looks like an unconfigured machine (show the wizard).

    True when there is no non-empty ``.env`` **and** no user-local setups —
    i.e. nothing the operator has configured yet. Either present ⇒ configured.
    """
    ep = env_path()
    has_env = ep.is_file() and ep.read_text(encoding="utf-8").strip() != ""
    udir = configs.user_setups_dir()
    has_user_setups = udir.is_dir() and any(udir.glob("*.yaml"))
    return not has_env and not has_user_setups


def read_env(path: str | os.PathLike | None = None) -> dict:
    """Parse ``KEY=value`` lines from ``.env`` into a dict (comments ignored)."""
    ep = Path(path) if path else env_path()
    out: dict = {}
    if not ep.is_file():
        return out
    for line in ep.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        out[key.strip()] = value.strip()
    return out


def write_env(updates: dict, path: str | os.PathLike | None = None) -> Path:
    """Update/append ``KEY=value`` lines in ``.env``, preserving the rest.

    Existing lines and comments are kept; a key already present is updated in
    place, otherwise it is appended. The file is written atomically and chmod
    ``0600`` (it carries bearer tokens). Values are never logged.

    Parameters
    ----------
    updates : dict
        ``{ENV_VAR: value}`` to set. Empty-string values are written (they
        clear a variable); drop a key to leave it untouched.
    path : str or os.PathLike, optional
        Target file; defaults to :func:`env_path`.

    Returns
    -------
    pathlib.Path
        The written path.
    """
    ep = Path(path) if path else env_path()
    ep.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        ep.read_text(encoding="utf-8").splitlines() if ep.is_file() else []
    )
    remaining = dict(updates)
    out = []
    updated = set()
    for line in existing:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append("{}={}".format(key, remaining.pop(key)))
                updated.add(key)
                continue
            if key in updated:
                # A stale duplicate of a key we already rewrote above — drop it
                # so a (malformed) .env with repeated keys collapses to one line
                # per key, and the update can't be shadowed by a later dupe.
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append("{}={}".format(key, value))
    text = "\n".join(out).rstrip("\n") + "\n"
    tmp = ep.with_name(ep.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, ep)
    try:
        os.chmod(ep, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - best effort (e.g. some Windows FS)
        pass
    logger.info("setup wizard: wrote {} ({} variables)", ep, len(updates))
    return ep


def enumerate_serial_ports() -> list:
    """Return available serial-port device names (best-effort, '' on none)."""
    try:
        from serial.tools import list_ports

        return [p.device for p in list_ports.comports()]
    except Exception as exc:  # noqa: BLE001 - enumeration is advisory
        logger.debug("serial-port enumeration failed: {!r}", exc)
        return []


def template_names() -> list:
    """Setups usable as a clone source (every discoverable setup)."""
    return configs.list_setups()


def _apply_override(data: dict, dotted_key: str, value) -> None:
    """Set ``data['a']['b']['c'] = value`` for ``dotted_key='a.b.c'``.

    Intermediate dicts are created as needed. A ``None`` value is ignored so a
    blank wizard field leaves the template's value untouched.
    """
    if value is None:
        return
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def clone_setup(src_name: str, new_name: str, overrides: dict | None = None):
    """Clone a template setup into a new on-disk setup dict.

    Reads the raw (un-normalised) source via
    :func:`PycroFlow.configs.load_setup_file`, sets the new ``setup`` name, and
    applies ``overrides`` (dotted keys, e.g.
    ``{'fluid.pumps.interface.COM': '3', 'illumination.config': 'Mercury'}``).
    Returns the on-disk dict ready for :func:`PycroFlow.configs.save_setup`;
    does not write anything.
    """
    import copy

    data = copy.deepcopy(configs.load_setup_file(src_name))
    data["setup"] = new_name
    for key, value in (overrides or {}).items():
        _apply_override(data, key, value)
    return data


def save_setup(name: str, data: dict) -> Path:
    """Persist a setup to the user setups dir (see configs.save_setup)."""
    return Path(configs.save_setup(name, data))


def check_registry(
    url: str | None, token: str | None = None, timeout: float = 3.0
) -> dict:
    """Probe a picasso-registry server's ``/health`` (best-effort).

    Returns ``{'ok': bool, 'detail': str}``; never raises. An empty ``url`` is
    reported as "not configured" (the registry is optional). ``requests`` is
    pulled in by ``picasso-registry[client]`` (a base dependency); if it is
    somehow absent the probe degrades to a clear message rather than failing.
    """
    if not url:
        return {"ok": False, "detail": "registry URL not set (optional)"}
    try:
        import requests
    except ImportError:  # pragma: no cover - requests ships with the client
        return {"ok": False, "detail": "requests not installed; cannot probe"}
    headers = {"Authorization": "Bearer {}".format(token)} if token else {}
    base = url.rstrip("/")
    try:
        resp = requests.get(base + "/health", headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - a probe failure isn't fatal
        return {"ok": False, "detail": "unreachable: {!r}".format(exc)}
    if resp.status_code == 200:
        return {"ok": True, "detail": "reachable ({})".format(base)}
    return {
        "ok": False,
        "detail": "HTTP {} from {}/health".format(resp.status_code, base),
    }


def check_monet_config(name: str | None) -> dict:
    """Whether ``name`` is a known ``monet.CONFIGS`` key (best-effort)."""
    if not name:
        return {"ok": False, "detail": "no monet config selected"}
    try:
        import monet
    except Exception as exc:  # noqa: BLE001 - monet is optional/absent
        return {
            "ok": False,
            "detail": "monet not importable ({!r})".format(exc),
        }
    configs_map = getattr(monet, "CONFIGS", {}) or {}
    if name in configs_map:
        return {
            "ok": True,
            "detail": "'{}' found in monet.CONFIGS".format(name),
        }
    return {
        "ok": False,
        "detail": "'{}' not in monet.CONFIGS ({} known)".format(
            name, len(configs_map)
        ),
    }


def validate_setup(data: dict) -> list:
    """Return a list of human-readable problems with a setup dict (empty = ok).

    A light structural check — the authoritative validation is still the
    loaders/`LegacyArchitecture` at connect time; this just catches the
    obvious wizard mistakes early (missing name, no fluid role, a real setup
    with no COM port).
    """
    problems = []
    if not data.get("setup"):
        problems.append("setup name is empty")
    fluid = data.get("fluid")
    if not isinstance(fluid, dict):
        problems.append("missing 'fluid' section")
        return problems
    if not data.get("emulated"):
        com = ((fluid.get("pumps") or {}).get("interface") or {}).get("COM")
        if com in (None, ""):
            problems.append(
                "non-emulated setup has no fluid.pumps.interface.COM port"
            )
    return problems
