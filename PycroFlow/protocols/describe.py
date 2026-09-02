"""Human-readable narration of a compiled Run Sequence.

Turns a compiled protocol (the per-subsystem ``$type`` entry lists) into an
ordered list of plain-English steps with their volumes and frame counts, e.g.::

    Pump 100 µl of Imager 1 into the sample
    Acquire 30000 frames (100 ms each)
    Pump 500 µl of Buffer into the sample
    Extract 600 µl from the sample

so the Experiment Design tab can preview what a design will actually do,
independent of the experiment type (it reads the compiled steps, not the
design).

The three subsystems (fluid / img / illu) run concurrently and coordinate via
``signal`` / ``wait for signal`` steps. To narrate them as one timeline the
steps are ordered by their *logical level* — the longest-path level over the
happens-before graph (program order within a subsystem plus signal→wait
edges), the same ordering the Run Sequence tab uses to correlate concurrent
steps. Coordination and instantaneous steps (signal, wait, set power/shutter)
carry no narration and are dropped.
"""

from __future__ import annotations

from PycroFlow.protocols.timing import format_duration, format_volume

# Subsystems that may carry narratable work, in tie-break display order.
_SYSTEMS = ("fluid", "img", "illu")


def _logical_levels(entries_by_system):
    """Longest-path happens-before level of every entry, per subsystem.

    Mirrors the Run Sequence tab's correlation ordering: program order within
    a subsystem, plus a ``signal`` value -> the ``wait for signal`` that
    consumes it.

    Parameters
    ----------
    entries_by_system : dict
        ``{system: [entry, ...]}``.

    Returns
    -------
    dict
        ``{system: [level, ...]}`` aligned with each entry list.
    """
    sigmap = {}
    for system in _SYSTEMS:
        for i, e in enumerate(entries_by_system.get(system) or []):
            if isinstance(e, dict) and e.get("$type") == "signal":
                val = e.get("value")
                if val is not None and val not in sigmap:
                    sigmap[val] = (system, i)
    levels = {s: [0] * len(entries_by_system.get(s) or []) for s in _SYSTEMS}
    total = sum(len(entries_by_system.get(s) or []) for s in _SYSTEMS)
    for _ in range(total + 1):
        changed = False
        for system in _SYSTEMS:
            entries = entries_by_system.get(system) or []
            for i, e in enumerate(entries):
                lvl = levels[system][i - 1] + 1 if i > 0 else 0
                if isinstance(e, dict) and e.get("$type") == "wait for signal":
                    dep = sigmap.get(e.get("value"))
                    if dep is not None:
                        ds, di = dep
                        lvl = max(lvl, levels[ds][di] + 1)
                if lvl != levels[system][i]:
                    levels[system][i] = lvl
                    changed = True
        if not changed:
            break
    return levels


def _reservoir_label(reservoir_id, reservoir_names):
    """Name a reservoir by id, falling back to ``reservoir <id>``."""
    if reservoir_names:
        # reservoir_names keys may be ints or their string form (YAML/json).
        name = reservoir_names.get(reservoir_id)
        if name is None and reservoir_id is not None:
            name = reservoir_names.get(str(reservoir_id))
        if name:
            return str(name)
    return "reservoir {}".format(reservoir_id)


def describe_entry(entry, reservoir_names=None):
    """One plain-English line for a protocol entry, or ``None`` to skip it.

    Parameters
    ----------
    entry : dict
        A protocol entry (needs ``$type`` and the type's fields).
    reservoir_names : dict, optional
        ``{reservoir_id: name}`` to name injected sources.
    """
    if not isinstance(entry, dict):
        return None
    type_ = entry.get("$type")
    if type_ == "inject":
        name = _reservoir_label(entry.get("reservoir_id"), reservoir_names)
        return "Pump {} of {} into the sample".format(
            format_volume(entry.get("volume")), name
        )
    if type_ == "pump_out":
        return "Extract {} from the sample".format(
            format_volume(entry.get("volume"))
        )
    if type_ == "incubate":
        return "Incubate for {}".format(format_duration(entry.get("duration")))
    if type_ == "acquire":
        frames = entry.get("frames") or 0
        t_exp = entry.get("t_exp")
        if t_exp:
            return "Acquire {} frames ({:g} ms each)".format(
                int(frames), float(t_exp)
            )
        return "Acquire {} frames".format(int(frames))
    return None


#: Entry ``$type``\ s that carry narration (everything else is dropped).
_NARRATABLE = ("inject", "pump_out", "incubate", "acquire")


def _coalesce(entries):
    """Merge adjacent same-action entries into one, summing their amounts.

    The builder emits an experiment step as several small entries (a main
    inject plus tiny priming/removal injects from the same reservoir); merging
    a run of same-``$type`` (and, for ``inject``, same-reservoir) entries keeps
    the narration one line per logical action — ``100 µl`` + ``1 µl`` of the
    same imager reads as ``101 µl``. ``acquire`` steps are never merged (each
    is a distinct movie).
    """
    merged = []
    for entry in entries:
        type_ = entry.get("$type")
        key = (
            (type_, entry.get("reservoir_id"))
            if type_ == "inject"
            else (type_,)
        )
        if merged and type_ != "acquire" and merged[-1][0] == key:
            merged[-1][1]["volume"] = (merged[-1][1].get("volume") or 0) + (
                entry.get("volume") or 0
            )
            merged[-1][1]["duration"] = (
                merged[-1][1].get("duration") or 0
            ) + (entry.get("duration") or 0)
            continue
        merged.append((key, dict(entry)))
    return [e for _key, e in merged]


def describe_protocol(protocol, reservoir_names=None):
    """Ordered plain-English narration of a compiled protocol.

    Adjacent same-action steps are merged (see :func:`_coalesce`) so the
    builder's small helper injects read as one line.

    Parameters
    ----------
    protocol : dict
        A compiled Run Sequence protocol
        (``{system: {'protocol_entries': [...]}}``).
    reservoir_names : dict, optional
        ``{reservoir_id: name}`` used to name injected reservoirs.

    Returns
    -------
    list of str
        One line per narratable action, in run order. Empty when the protocol
        has no such steps.
    """
    protocol = protocol or {}
    entries_by_system = {
        s: (protocol.get(s) or {}).get("protocol_entries") or []
        for s in _SYSTEMS
    }
    levels = _logical_levels(entries_by_system)
    ordered = []
    for system in _SYSTEMS:
        for i, entry in enumerate(entries_by_system[system]):
            if isinstance(entry, dict) and entry.get("$type") in _NARRATABLE:
                ordered.append(
                    (levels[system][i], _SYSTEMS.index(system), entry)
                )
    ordered.sort(key=lambda t: (t[0], t[1]))
    lines = []
    for entry in _coalesce([e for _lvl, _so, e in ordered]):
        line = describe_entry(entry, reservoir_names)
        if line is not None:
            lines.append(line)
    return lines
