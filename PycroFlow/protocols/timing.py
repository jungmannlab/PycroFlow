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

This is deliberately an estimate, but it models the dominant per-step
overheads (valve switching, serial round-trips, the concurrently-driven
extraction pump, camera arm/readout) as fixed constants on top of the ideal
fluid-motion / exposure time, so callers should still present the numbers as
approximate (the GUI prefixes them with ``~``). The constants below were
calibrated from ``STEP_TIMING`` run logs (see
:mod:`PycroFlow.protocols.timing_analysis`) — pure fluid-motion / exposure
time underestimated real steps badly, especially short ones (a 1 µl inject
takes ~3 s of valve/serial overhead but only ~0.01 s of motion). They can be
overridden per setup via the owning subsystem's ``parameters`` block using the
``est_*`` keys named on each constant.
"""

from __future__ import annotations

# Subsystems that may carry timed work, in display order.
_SYSTEMS = ("fluid", "img", "illu")

#: Marker prefixing the per-step timing records written to the run log by
#: ``AbstractSystemHandler._log_step_timing``. Each tagged line is followed by
#: a JSON object holding the measured and estimated duration of one step;
#: :mod:`PycroFlow.protocols.timing_analysis` mines them to score and improve
#: the estimates below.
STEP_TIMING_TAG = 'STEP_TIMING'

# --- calibrated per-step overheads (seconds) -------------------------------
# Measured from lab STEP_TIMING logs (ibidi multiplexer + Hamilton PSD/MVP).
# Each is overridable via the subsystem ``parameters`` block under the given
# ``est_*`` key once timing_analysis calibrates a specific setup.

#: Fixed cost of an ``inject`` beyond fluid motion: the ibidi channel
#: switching (~24 ``SET:Valve`` commands), the Hamilton pump-valve rotations,
#: and the extraction pump driven in parallel. Override: ``est_inject_overhead``.
DEFAULT_INJECT_OVERHEAD_S = 3.45
#: Fixed cost of a standalone ``pump_out``. Override: ``est_pumpout_overhead``.
DEFAULT_PUMPOUT_OVERHEAD_S = 1.6
#: Per-acquisition arm/ZMQ/PFS startup before frames stream.
#: Override: ``est_acquire_setup``.
DEFAULT_ACQUIRE_SETUP_S = 2.0
#: Camera readout/transfer per frame *beyond* the exposure time.
#: Override: ``est_frame_overhead``.
DEFAULT_FRAME_OVERHEAD_S = 0.09


def _param(parameters, key, default):
    """Read a numeric override from a parameters block, else ``default``."""
    try:
        val = parameters.get(key)
        return float(val) if val is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def estimate_entry_duration(entry, parameters=None):
    """Estimate one protocol entry's wall-clock duration, in seconds.

    Adds the calibrated fixed overheads (see the module docstring) to the
    ideal fluid-motion / exposure time. Returns ``0.0`` for coordination /
    instantaneous steps and for entries whose timing parameters are missing.

    Parameters
    ----------
    entry : dict
        A protocol entry (needs ``$type`` and the type's timing fields).
    parameters : dict, optional
        The owning subsystem's ``parameters`` block, used for the fluid
        fallback velocity, the inject equilibration delays, and the optional
        ``est_*`` overhead overrides.
    """
    if not isinstance(entry, dict):
        return 0.0
    parameters = parameters or {}
    type_ = entry.get("$type")
    if type_ == "acquire":
        frames = float(entry.get("frames") or 0)
        t_exp = float(entry.get("t_exp") or 0)  # milliseconds per frame
        if frames <= 0:
            return 0.0
        frame_overhead = _param(
            parameters, "est_frame_overhead", DEFAULT_FRAME_OVERHEAD_S
        )
        setup = _param(
            parameters, "est_acquire_setup", DEFAULT_ACQUIRE_SETUP_S
        )
        # Each frame costs its exposure plus camera readout/transfer, and the
        # whole acquisition has a fixed arm/PFS/ZMQ startup.
        return frames * (t_exp / 1000.0 + frame_overhead) + setup
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
            seconds += _param(
                parameters, "est_inject_overhead", DEFAULT_INJECT_OVERHEAD_S
            )
        else:
            seconds += _param(
                parameters, "est_pumpout_overhead", DEFAULT_PUMPOUT_OVERHEAD_S
            )
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


def estimate_volumes(protocol):
    """Total liquid volumes a compiled protocol consumes, in µl.

    Sums the fluid ``inject`` volumes per source reservoir (what must be
    loaded) and the total waste extracted. Every ``inject`` *also* runs the
    extraction pump at the same time, removing ``extractionfactor × volume``
    from the sample; standalone ``pump_out`` steps do the same. So the waste
    total counts the simultaneous extraction of each inject **and** the
    pump-outs — using each entry's own ``extractionfactor`` when it sets one
    (e.g. the ``0`` re-inject that pushes liquid back without extracting) and
    otherwise the fluid ``parameters['extractionfactor']`` (default 1).

    Parameters
    ----------
    protocol : dict
        A compiled Run Sequence protocol (see :func:`estimate_durations`).

    Returns
    -------
    dict
        ``{'per_reservoir': {reservoir_id: µl}, 'total_injected': µl,
        'total_waste': µl}``. ``per_reservoir`` omits reservoirs never
        injected; keys are the ids used in the fluid entries. ``total_waste``
        typically exceeds ``total_injected`` when the extraction factor is
        greater than 1.
    """
    per_reservoir = {}
    total_injected = 0.0
    total_waste = 0.0
    fluid = (protocol or {}).get("fluid")
    if not isinstance(fluid, dict):
        return {
            "per_reservoir": per_reservoir,
            "total_injected": total_injected,
            "total_waste": total_waste,
        }
    entries = fluid.get("protocol_entries") or []
    params = fluid.get("parameters") or {}
    try:
        default_ef = float(params.get("extractionfactor", 1))
    except (TypeError, ValueError):
        default_ef = 1.0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        type_ = entry.get("$type")
        if type_ not in ("inject", "pump_out"):
            continue
        try:
            vol = float(entry.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        ef = entry.get("extractionfactor")
        try:
            ef = float(ef) if ef is not None else default_ef
        except (TypeError, ValueError):
            ef = default_ef
        # Every inject/pump_out extracts extractionfactor * volume to waste.
        total_waste += ef * vol
        if type_ == "inject":
            rid = entry.get("reservoir_id")
            per_reservoir[rid] = per_reservoir.get(rid, 0.0) + vol
            total_injected += vol
    return {
        "per_reservoir": per_reservoir,
        "total_injected": total_injected,
        "total_waste": total_waste,
    }


def format_volume(microliters):
    """Compact human-readable volume, e.g. ``'750 µl'`` / ``'2.5 ml'``.

    Falls back to ``'0 µl'`` for zero/negative input.
    """
    microliters = max(float(microliters or 0), 0.0)
    if microliters <= 0:
        return "0 µl"
    if microliters < 1000:
        return "{:d} µl".format(int(round(microliters)))
    ml = microliters / 1000.0
    # Whole millilitres drop the decimals; otherwise show two places.
    if abs(ml - round(ml)) < 1e-9:
        return "{:d} ml".format(int(round(ml)))
    return "{:.2f} ml".format(ml)


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
