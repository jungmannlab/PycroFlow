"""The C32 contribution contract: what a module gives the composable shell.

The shell (:mod:`PycroFlow.gui.live.shell`) does not know about specific tabs.
It mounts whatever *contributions* the active modules declare — registry-only
install -> dashboard only; acquisition rig -> operator tabs; full loop ->
everything (C32). A module contributes by providing a :class:`PanelContributor`;
the shell asks each contributor for its :class:`PanelSpec`\\ s and mounts them.

**Discovery mechanism** (the build-time open sub-question in the brief): a
**declared panel registry** — a module registers a contributor via
:func:`register_contributor`, and the shell reads :func:`iter_contributors`.
Chosen over entry-point plugin discovery for now because (a) all first-party
modules live in one process, (b) it is trivially testable headlessly with no
packaging, and (c) an entry-point loader can later be added as *another* source
that calls :func:`register_contributor`, so this stays the single mount point.

Each :class:`PanelSpec` names a **group** (which top-level tab group it belongs
to — ``"Setup" / "Live QC" / "Analysis" / "Assistant"`` for the operator module,
or ``"__sidebar__"`` for a sidebar contribution) and a factory that builds the
widget given the shell context (the :class:`~PycroFlow.gui.live.client.LiveClient`
and the advisor). Widgets that want stream updates implement
:class:`StreamConsumer` (an ``on_update`` slot the shell connects to the client).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol, runtime_checkable

# Reserved group name for sidebar contributions (the fixed QC-at-a-glance rail).
SIDEBAR_GROUP = "__sidebar__"

# The operator module's four top-level tab groups, in display order (C31).
OPERATOR_GROUPS = ("Setup", "Live QC", "Analysis", "Assistant")


@dataclass
class ShellContext:
    """What the shell hands each panel factory when it mounts it.

    Panels bind to the :class:`~PycroFlow.gui.live.client.LiveClient` interface
    (transport-agnostic) and the advisor, never to the concrete service.
    """

    client: Any  # LiveClient
    advisor: Any  # advisor.Advisor
    extra: dict = field(default_factory=dict)


@dataclass
class PanelSpec:
    """A single contributed panel/tab.

    Parameters
    ----------
    key : str
        Stable id (used in tests + object names).
    title : str
        Tab / panel label.
    group : str
        Top-level group it mounts under (one of :data:`OPERATOR_GROUPS`, a custom
        dashboard group, or :data:`SIDEBAR_GROUP`).
    factory : Callable[[ShellContext], QWidget]
        Builds the widget. May return a :class:`StreamConsumer`.
    order : int
        Sort key within its group (lower first).
    """

    key: str
    title: str
    group: str
    factory: Callable[["ShellContext"], Any]
    order: int = 0


@runtime_checkable
class StreamConsumer(Protocol):
    """A mounted widget that wants live-stream updates.

    The shell connects the client's ``update`` signal to :meth:`on_update`, so a
    consumer receives every :class:`~PycroFlow.live_analysis.client_seam.LiveUpdate`
    on the GUI thread.
    """

    def on_update(self, update: Any) -> None: ...


class PanelContributor(Protocol):
    """A module's contribution of panels to the shell (C32)."""

    module_id: str

    def panels(self) -> List[PanelSpec]: ...


# -- declared panel registry ------------------------------------------------
_CONTRIBUTORS: "List[PanelContributor]" = []


def register_contributor(contributor: "PanelContributor") -> None:
    """Register a module's contributor (idempotent per ``module_id``)."""
    existing = {c.module_id for c in _CONTRIBUTORS}
    if contributor.module_id not in existing:
        _CONTRIBUTORS.append(contributor)


def unregister_contributor(module_id: str) -> None:
    """Remove a contributor by id (used by tests to isolate registrations)."""
    _CONTRIBUTORS[:] = [c for c in _CONTRIBUTORS if c.module_id != module_id]


def iter_contributors() -> "List[PanelContributor]":
    """The registered contributors, in registration order."""
    return list(_CONTRIBUTORS)


def clear_contributors() -> None:
    """Drop all registrations (test hygiene)."""
    _CONTRIBUTORS.clear()


def collect_panels(
    contributors: "Optional[List[PanelContributor]]" = None,
) -> List[PanelSpec]:
    """Flatten every contributor's panels, sorted by (group order, order, key).

    Group order follows :data:`OPERATOR_GROUPS`; unknown groups sort after the
    known ones (stable, alphabetical) so a later dashboard/console module can add
    its own groups without editing the operator module.
    """
    contributors = (
        iter_contributors() if contributors is None else contributors
    )
    specs: List[PanelSpec] = []
    for c in contributors:
        specs.extend(c.panels())

    def group_rank(g: str) -> tuple:
        if g == SIDEBAR_GROUP:
            return (-1, "")
        if g in OPERATOR_GROUPS:
            return (OPERATOR_GROUPS.index(g), "")
        return (len(OPERATOR_GROUPS), g)

    return sorted(specs, key=lambda s: (group_rank(s.group), s.order, s.key))
