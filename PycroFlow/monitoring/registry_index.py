"""Best-effort registry indexing of monitoring clips.

Each recorded round-clip's pool URI is posted onto a ``fluidics_round`` record
in the picasso-registry so the movie is discoverable alongside the run. The URI
travels as a top-level ``monitoring_video_uri`` field; the registry's
``extra='allow'`` schema folds it into the row's ``extra`` JSON today, and a
future typed column would populate transparently -- no registry change is
required for this to work.

Everything here is **best-effort**: a down, slow, or absent registry (or a
missing ``picasso-registry`` install) degrades to a logged no-op and never
blocks the recording or the run. When enabled from the environment it uses the
:class:`BufferedRegistryClient`, whose writes are durably buffered on disk and
replayed by a background thread, so even a reachable-but-slow registry cannot
stall the caller.

Enable by setting ``PAINT_REGISTRY_URL`` (and ``PAINT_REGISTRY_TOKEN`` on a
networked/authenticated registry, per ADR 001 / C18). Unset => indexing off.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

RESOURCE = "fluidics_round"


class RegistryIndexWriter:
    """Post monitoring-clip URIs onto ``fluidics_round`` rows, best-effort.

    Parameters
    ----------
    run_id : str
        The run these clips belong to (written as ``acquisition_run_id``).
    client : object or None
        A registry client exposing ``create(resource, **fields)`` (e.g.
        ``RegistryClient`` / ``BufferedRegistryClient`` / the test
        ``MockRegistryClient``). ``None`` disables indexing.
    owns_client : bool
        When True, :meth:`close` closes the client (it was built here).
    """

    def __init__(
        self,
        run_id: str,
        client: Optional[Any] = None,
        *,
        owns_client: bool = False,
    ):
        self.run_id = run_id
        self._client = client
        self._owns_client = owns_client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_env(
        cls, run_id: str, *, buffer_path: Optional[str] = None
    ) -> "RegistryIndexWriter":
        """Build from ``PAINT_REGISTRY_URL`` / ``PAINT_REGISTRY_TOKEN``.

        Returns a disabled writer (indexing off) when the URL is unset or the
        ``picasso-registry`` client is not installed -- never raises.
        """
        url = os.environ.get("PAINT_REGISTRY_URL")
        if not url:
            logger.info(
                "monitoring: PAINT_REGISTRY_URL unset; clip indexing disabled"
            )
            return cls(run_id, None)
        try:
            from picasso_registry.buffered_client import (
                BufferedRegistryClient,
            )
        except Exception as exc:  # ImportError or a broken install
            logger.warning(
                "monitoring: picasso-registry client unavailable "
                "({!r}); clip indexing disabled",
                exc,
            )
            return cls(run_id, None)
        token = os.environ.get("PAINT_REGISTRY_TOKEN")
        buf = buffer_path or os.environ.get(
            "PYCROFLOW_REGISTRY_BUFFER", "registry_buffer.sqlite"
        )
        try:
            client = BufferedRegistryClient(url, buffer_path=buf, token=token)
        except Exception as exc:  # never let registry setup break capture
            logger.warning(
                "monitoring: could not start registry client ({!r}); "
                "clip indexing disabled",
                exc,
            )
            return cls(run_id, None)
        logger.info("monitoring: clip indexing -> {}", url)
        return cls(run_id, client, owns_client=True)

    def index(
        self,
        round_index: int,
        uri: str,
        *,
        round_name: Optional[str] = None,
        protocol_step: Optional[int] = None,
    ) -> Optional[dict]:
        """Post one clip URI onto its ``fluidics_round``. Never raises.

        Returns the created record dict, or ``None`` when indexing is disabled
        or the post failed (logged).
        """
        if self._client is None:
            return None
        fields: dict[str, Any] = {
            "acquisition_run_id": self.run_id,
            "round_index": round_index,
            "monitoring_video_uri": uri,
        }
        if round_name is not None:
            fields["round_name"] = round_name  # unknown key -> extra JSON
        if protocol_step is not None:
            fields["protocol_step"] = protocol_step  # unknown key -> extra
        try:
            return self._client.create(RESOURCE, **fields)
        except Exception as exc:  # best-effort: a bad registry never blocks
            logger.warning(
                "monitoring: failed to index round {} clip ({!r})",
                round_index,
                exc,
            )
            return None

    def close(self) -> None:
        """Flush/close the client if this writer owns it. Never raises."""
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "monitoring: registry client close failed ({!r})", exc
                    )
