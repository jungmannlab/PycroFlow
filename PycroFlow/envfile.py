"""Best-effort `.env` loading for the PycroFlow frontends.

The repo ships a tracked `.env.template`; a filled-in, GITIGNORED `.env` in
the repo root (or any parent of the working directory) carries the
per-machine secrets and paths — the registry URL/bearer token
(``PAINT_REGISTRY_*``), monet's config paths and token, the spill-sensor
port. The frontends call :func:`load_env_file` at startup so those variables
reach ``os.environ`` without relying on monet's import-time dotenv load (a
registry-only or emulated machine has no monet).

Semantics match monet's loader: ``override=False`` — an already-exported
shell variable or a systemd ``EnvironmentFile`` always wins — and a missing
``python-dotenv`` (not a PycroFlow base dependency; monet brings it) or a
missing ``.env`` degrades to a logged no-op, never an error.
"""

from __future__ import annotations

from loguru import logger


def load_env_file(path: str | None = None) -> bool:
    """Load `.env` into the environment, best-effort.

    Parameters
    ----------
    path : str or None
        Explicit file to load; None searches from the working directory
        upward (python-dotenv's discovery).

    Returns
    -------
    bool
        True when a file was found and loaded.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        logger.debug("python-dotenv not installed; .env loading skipped")
        return False
    try:
        found = path or find_dotenv(usecwd=True)
        if not found:
            return False
        loaded = load_dotenv(found, override=False)
        if loaded:
            logger.info("environment loaded from {}", found)
        return bool(loaded)
    except Exception as exc:  # noqa: BLE001 - .env must never block startup
        logger.warning("could not load .env ({!r})", exc)
        return False
