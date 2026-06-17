"""Valve abstraction.

Multi-position rotary valves selecting between reservoir lines or pump
directions. The existing :class:`PycroFlow.hamilton_components.Valve`
(Hamilton MVP) matches this interface.
"""
from __future__ import annotations

import abc


class Valve(abc.ABC):
    """A multi-position fluid valve."""

    @abc.abstractmethod
    def set_valve(self, pos, move_now: bool = True) -> None:
        """Switch the valve to position ``pos``."""

    @abc.abstractmethod
    def wait_until_done(self) -> None:
        """Block until the valve has reached its commanded position."""

    @abc.abstractmethod
    def get_status(self):
        """Return a vendor-specific status descriptor (string or struct)."""
