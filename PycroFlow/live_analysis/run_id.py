"""Mint the ULID ``run_id`` the whole automation loop joins on.

A single sortable ULID is minted at experiment start and tagged onto every FOV
and every registry record, so acquisition / analysis / metrics rows all join by
``run_id`` (the registry's join key, register spine). We mint the *same* flavour
of id the registry mints server-side (``python-ulid``'s ``str(ULID())``) so ids
are byte-for-byte compatible across the wire.
"""

from __future__ import annotations


def new_run_id() -> str:
    """Return a fresh, sortable ULID string (matches the registry's ids)."""
    from ulid import ULID

    return str(ULID())
