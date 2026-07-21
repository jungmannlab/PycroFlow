"""Estimate Run Sequence durations for the GUI progress / ETA displays.

Pure helpers (no hardware, no Qt) that estimate how long a compiled Run
Sequence protocol takes, so the GUI can show a total before the run starts, a
remaining-time estimate while it runs, and a pre-run estimate in the
Experiment Design tab.

The three subsystems (fluid / img / illu) coordinate via ``signal`` /
``wait for signal`` steps, so their *active* work does not meaningfully
overlap (fluid prepares a round, imaging then acquires it, fluid then washes).
Coordination and instantaneous steps (signal, wait, set power, ...) therefore
contribute no time, and the wall-clock estimate is the sum of the active-work
durations across all subsystems.

This is deliberately an estimate: valve moves, serial round-trips and pump
ramp-up are not modelled, so callers should present the numbers as
approximate (the GUI prefixes them with ``~``). The per-entry inject/pump_out
model mirrors
:meth:`PycroFlow.fluid.legacy.LegacyFluidHandler._estimate_entry_duration`.
"""

from __future__ import annotations

# Subsystems that may carry timed work, in display order.
_SYSTEMS = ("fluid", "img", "illu")


def estimate_entry_duration(entry, parameters=None):
    """Estimate one protocol entry's wall-clock duration, in seconds.

    Returns ``0.0`` for coordination / instantaneous steps and for entries
    whose timing parameters are missing.

    Parameters
    ----------
    entry : dict
        A protocol entry (needs ``$type`` and the type's timing fields).
    parameters : dict, optional
        The owning subsystem's ``parameters`` block, used for the fluid
        fallback velocity and the inject equilibration delays.
    """
    if not isinstance(entry, dict):
        return 0.0
    parameters = parameters or {}
    type_ = entry.get("$type")
    if type_ == "acquire":
        frames = entry.get("frames") or 0
        t_exp = entry.get("t_exp") or 0  # milliseconds per frame
        return float(frames) * float(t_exp) / 1000.0
    if type_ == "incubate":
        try:
            return max(float(entry.get("duration") or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0
    if type_ in ("inject", "pump_out"):
        vol = entry.get("volume")
        velocity = entry.get("velocity") or parameters.get("max_velocity")
        if not vol or not velocity:
            return 0.0
        # Pick the volume up and dispense it: ~2 * volume / velocity minutes.
        seconds = 120.0 * float(vol) / float(velocity)
        if type_ == "inject":
            seconds += parameters.get("inject_in_to_out_delay", 0) or 0
            seconds += parameters.get("inject_out_to_in_delay", 0) or 0
            seconds += 2 * (entry.get("delay", 0) or 0)
        return max(seconds, 0.0)
    return 0.0


def estimate_durations(protocol):
    """Per-entry duration estimates for each subsystem.

    Parameters
    ----------
    protocol : dict
        A compiled Run Sequence protocol
        (``{system: {'protocol_entries': [...], 'parameters': {...}}}``).

    Returns
    -------
    dict
        ``{system: [seconds, ...]}`` aligned with each subsystem's
        ``protocol_entries``. Subsystems absent from ``protocol`` are omitted.
    """
    out = {}
    protocol = protocol or {}
    for system in _SYSTEMS:
        sub = protocol.get(system)
        if not isinstance(sub, dict):
            continue
        entries = sub.get("protocol_entries") or []
        params = sub.get("parameters") or {}
        out[system] = [estimate_entry_duration(e, params) for e in entries]
    return out


def estimate_total_duration(protocol):
    """Estimated wall-clock duration of the whole protocol, in seconds."""
    return sum(sum(durs) for durs in estimate_durations(protocol).values())


def estimate_remaining(durations, current):
    """Seconds of work left, summed across subsystems.

    Parameters
    ----------
    durations : dict
        ``{system: [seconds, ...]}`` from :func:`estimate_durations`.
    current : dict
        ``{system: (current_index, total)}`` (the GUI ``progress()`` map);
        entries at or after ``current_index`` are counted as remaining.
    """
    remaining = 0.0
    for system, durs in durations.items():
        cur = current.get(system, (0, 0))[0] if current else 0
        remaining += sum(durs[max(cur, 0) :])
    return remaining


def format_duration(seconds):
    """Compact human-readable duration, e.g. ``'2h 5m'`` / ``'45s'``.

    Rounds to whole units, falls back to ``'0s'`` for zero/negative input.
    """
    seconds = max(float(seconds or 0), 0.0)
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return "{:d}s".format(int(round(seconds)))
    mins = int(seconds // 60)
    if mins < 60:
        return "{:d}m".format(mins)
    hrs, mins = divmod(mins, 60)
    if hrs < 24:
        return "{:d}h {:d}m".format(hrs, mins)
    days, hrs = divmod(hrs, 24)
    return "{:d}d {:d}h".format(days, hrs)
