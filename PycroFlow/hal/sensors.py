"""Sensor abstractions.

Currently only :class:`SpillSensor`; future leak detectors, pressure
sensors, etc. can subclass without changing orchestration code.
"""

from __future__ import annotations

import abc
from typing import Callable, Optional


class SpillSensor(abc.ABC):
    """Wet/dry detection. Implementations may poll or stream over serial."""

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish hardware connection. Return True iff successful."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Close the hardware connection."""

    @abc.abstractmethod
    def poll_sensor(self) -> Optional[bool]:
        """One-shot read. Returns True (wet) / False (dry) / None (error)."""

    @abc.abstractmethod
    def monitor_sensor(
        self, fn_on_wet: Optional[Callable[[str], None]] = None
    ) -> None:
        """Start a background monitoring thread that invokes ``fn_on_wet``
        when a wet reading is observed."""

    @abc.abstractmethod
    def stop_monitoring(self) -> None:
        """Stop the background monitor and join its thread."""
