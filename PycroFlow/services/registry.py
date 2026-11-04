"""Shared picasso-registry client construction (env-configured, optional).

One place builds the instrument-side registry client so every PycroFlow
writer (the live-analysis records today, monitoring-clip indexing when it
lands) shares one convention:

* ``PAINT_REGISTRY_URL`` — the registry base URL; unset means registry use
  is DISABLED (the factory returns None and callers run unchanged).
* ``PAINT_REGISTRY_TOKEN`` — optional bearer token (A9/C18: env or
  per-machine gitignored secrets only — never committed, never logged).
* ``PYCROFLOW_REGISTRY_BUFFER`` — path of the on-disk write buffer
  (default ``registry_buffer.sqlite`` in the working directory).

The client is the WP-3 ``BufferedRegistryClient``: writes land in a durable
SQLite buffer and a background thread replays them, so a registry outage
never stalls or fails a run on the instrument PC. The factory itself never
raises — any setup problem logs a warning and disables registry use.
"""

from __future__ import annotations

import os

from loguru import logger


def registry_client_from_env(*, buffer_path: str | None = None):
    """Build a ``BufferedRegistryClient`` from the environment.

    Parameters
    ----------
    buffer_path : str or None
        Overrides the ``PYCROFLOW_REGISTRY_BUFFER`` env var / default.

    Returns
    -------
    object or None
        The buffered client, or None when ``PAINT_REGISTRY_URL`` is unset,
        the ``picasso-registry`` client is not installed, or construction
        fails — callers treat None as "registry disabled".
    """
    url = os.environ.get("PAINT_REGISTRY_URL")
    if not url:
        logger.info(
            "registry: PAINT_REGISTRY_URL unset; registry writes disabled"
        )
        return None
    try:
        from picasso_registry.buffered_client import BufferedRegistryClient
    except Exception as exc:  # ImportError or a broken install
        logger.warning(
            "registry: picasso-registry client unavailable ({!r}); "
            "registry writes disabled",
            exc,
        )
        return None
    token = os.environ.get("PAINT_REGISTRY_TOKEN")
    buf = buffer_path or os.environ.get(
        "PYCROFLOW_REGISTRY_BUFFER", "registry_buffer.sqlite"
    )
    try:
        client = BufferedRegistryClient(url, buffer_path=buf, token=token)
    except Exception as exc:  # never let registry setup break a run
        logger.warning(
            "registry: could not start the registry client ({!r}); "
            "registry writes disabled",
            exc,
        )
        return None
    logger.info("registry: writes -> {}", url)
    return client
