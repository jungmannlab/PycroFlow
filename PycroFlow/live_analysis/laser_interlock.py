"""Laser fail-safe interlock (T3) — lasers off + shutter closed on any exit.

Ratified as decision C21 (precondition A10). The early-abort path AND any
error/crash exit of a live-analysis FOV go through this interlock, which
disables every laser (monet's per-laser ``enabled`` setter) and closes the
shutter. It is **fail-safe**: every step is wrapped so a failure to reach one
laser still tries the rest and still closes the shutter, and the interlock must
NEVER raise into — or block — the acquisition path (a caller runs it inside a
``finally``). Automation defaults ``lasers_off_finally`` ON.

The interlock talks to :class:`PycroFlow.illumination.IlluminationSystem`, which
owns the monet laser control (``instrument.lasers[i].enabled = False`` per laser,
``beampath_close()`` for the shutter). It is deliberately duck-typed on that
surface so a test can pass a fake illumination system with the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass
class InterlockResult:
    """Outcome of one interlock run (for logging / the registry record / tests)."""

    attempted: bool
    lasers_disabled: list
    lasers_failed: list
    shutter_closed: bool
    errors: list

    @property
    def safe(self) -> bool:
        """True if every laser reached is off and the shutter is closed."""
        return (
            self.attempted and not self.lasers_failed and self.shutter_closed
        )


class LaserInterlock:
    """Disable lasers + close the shutter, fail-safe and non-blocking.

    Parameters
    ----------
    illumination_system : object or None
        A :class:`PycroFlow.illumination.IlluminationSystem` (or a duck-typed
        stand-in). ``None`` makes the interlock a safe no-op (nothing to disable),
        which is the correct behaviour when illumination is not wired.
    enabled : bool
        Master switch (``lasers_off_finally``); default ON. When False the
        interlock records that it did not run rather than touching hardware.
    """

    def __init__(self, illumination_system=None, *, enabled: bool = True):
        self._illu = illumination_system
        self.enabled = enabled

    def engage(self, *, reason: str = "") -> InterlockResult:
        """Disable all lasers and close the shutter. Never raises.

        Safe to call more than once (idempotent: re-disabling an off laser and
        re-closing a closed shutter are both no-ops on the hardware).
        """
        if not self.enabled or self._illu is None:
            logger.debug(
                "laser interlock skipped (enabled={}, illu={})".format(
                    self.enabled, self._illu is not None
                )
            )
            return InterlockResult(
                attempted=False,
                lasers_disabled=[],
                lasers_failed=[],
                shutter_closed=False,
                errors=[],
            )

        logger.warning(
            "engaging laser interlock (lasers off + shutter close){}".format(
                ": " + reason if reason else ""
            )
        )
        disabled: list = []
        failed: list = []
        errors: list = []

        for laser in self._iter_lasers(errors):
            try:
                self._disable_one(laser)
                disabled.append(laser)
            except Exception as exc:  # noqa: BLE001 - fail-safe: try the rest
                failed.append(laser)
                errors.append("laser {!r}: {!r}".format(laser, exc))
                logger.error(
                    "interlock: could not disable laser {!r}: {!r}".format(
                        laser, exc
                    )
                )

        shutter_closed = self._close_shutter(errors)

        result = InterlockResult(
            attempted=True,
            lasers_disabled=disabled,
            lasers_failed=failed,
            shutter_closed=shutter_closed,
            errors=errors,
        )
        if result.safe:
            logger.info(
                "laser interlock: all lasers off, shutter closed "
                "(disabled {})".format(disabled)
            )
        else:
            logger.error(
                "laser interlock did NOT fully succeed: {}".format(result)
            )
        return result

    # -- internals (each isolated so one failure can't abort the rest) -------

    def _iter_lasers(self, errors: list) -> list:
        """Return the laser identifiers to disable, tolerating a missing API.

        Prefers the monet ``instrument.lasers`` mapping (keys are the laser
        identifiers) so we disable every line, not just the active one.
        """
        illu = self._illu
        instrument = getattr(illu, "instrument", None)
        lasers = getattr(instrument, "lasers", None)
        if lasers is not None:
            try:
                return list(lasers.keys())
            except Exception as exc:  # noqa: BLE001
                errors.append("enumerating lasers: {!r}".format(exc))
        # Fall back to the single currently-active laser if that's all we can see.
        curr = getattr(instrument, "curr_laser", None)
        if curr is not None:
            return [curr]
        errors.append("no laser instrument available to enumerate")
        return []

    def _disable_one(self, laser) -> None:
        """Disable one laser via the illumination system's setter."""
        illu = self._illu
        setter = getattr(illu, "set_laser_enabled", None)
        if callable(setter):
            setter(laser, False)
            return
        # Direct monet fall-back: instrument.lasers[i].enabled = False.
        self._illu.instrument.lasers[laser].enabled = False

    def _close_shutter(self, errors: list) -> bool:
        """Close the shutter; return True on success."""
        illu = self._illu
        closer = getattr(illu, "beampath_close", None)
        if not callable(closer):
            errors.append("illumination system has no beampath_close()")
            return False
        try:
            closer()
            return True
        except Exception as exc:  # noqa: BLE001 - fail-safe
            errors.append("closing shutter: {!r}".format(exc))
            logger.error(
                "interlock: could not close shutter: {!r}".format(exc)
            )
            return False
