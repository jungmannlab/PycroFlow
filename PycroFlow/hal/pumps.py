"""Pump abstraction.

The minimum surface area called by high-level fluid orchestration. The
existing :class:`PycroFlow.hamilton_components.Pump` (Hamilton PSD) and
:class:`PycroFlow.peristaltic_drifton.DriftonPump` (Drifton peristaltic)
already match this duck-typed interface; the ABC pins the contract.
"""
from __future__ import annotations

import abc
from typing import Optional


class Pump(abc.ABC):
    """A liquid-handling pump that aspirates and dispenses fixed volumes."""

    @abc.abstractmethod
    def pickup(
        self,
        vol: float,
        velocity: Optional[float] = None,
        waitForPump: bool = False,
        override_pause_flag: bool = False,
    ) -> None:
        """Aspirate ``vol`` µL through the configured input valve position.

        ``velocity`` overrides the default flow rate (µL/min). Setting
        ``waitForPump`` blocks until the move completes; otherwise the call
        returns immediately and the move runs asynchronously.
        """

    @abc.abstractmethod
    def dispense(
        self,
        vol: float,
        velocity: Optional[float] = None,
        waitForPump: bool = False,
        override_pause_flag: bool = False,
    ) -> None:
        """Dispense ``vol`` µL through the configured output valve position."""

    @abc.abstractmethod
    def set_valve(self, pos, move_now: bool = True) -> None:
        """Switch the pump's integrated valve to ``pos``."""

    @abc.abstractmethod
    def wait_until_done(self) -> None:
        """Block until any in-flight move completes."""

    @abc.abstractmethod
    def stop_current_move(self) -> None:
        """Abort the in-flight move (used on pause / abort)."""

    @abc.abstractmethod
    def get_current_volume(self) -> float:
        """Return the current syringe volume (µL)."""
