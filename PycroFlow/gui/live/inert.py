"""Explicit "planned — not yet wired" marking for live-GUI controls.

A control that does nothing (a button with no handler, a placeholder panel) reads
as a *bug* during testing — the tester can't tell "not implemented yet" from
"broken". Mark it with :func:`mark_inert` so it is:

* **visible** — the ``[inert="true"]`` stylesheet rule (see
  :data:`PycroFlow.gui.live.theme.LIVE_QSS`) dims it, dashes its border and
  italicises it, and the tooltip says what will wire it;
* **queryable** — the ``inert`` dynamic property, which the pinned test
  ``test_live_gui.test_inert_controls_are_flagged`` asserts against a fixed
  expected set.

The forcing function: when a control is wired **along the plan**, you delete its
``mark_inert(...)`` call — which removes it from the property set — and the
pinned test then FAILS until you also remove it from that test's expected list.
So the flag can neither silently outlive the wiring (a wired-but-still-flagged
control) nor linger unnoticed. Keep the ``note`` pointing at the WP/plan item
that will activate it.
"""

from __future__ import annotations

INERT_PROPERTY = "inert"


def mark_inert(widget, note: str = ""):
    """Flag ``widget`` as planned-but-not-yet-wired. Returns it (chainable)."""
    widget.setProperty(INERT_PROPERTY, True)
    tip = "Planned — not yet wired"
    if note:
        tip += " ({})".format(note)
    widget.setToolTip(tip)
    # Re-evaluate the [inert="true"] stylesheet rule now the property is set
    # (dynamic-property selectors need an unpolish/polish to take effect).
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def is_inert(widget) -> bool:
    return bool(widget.property(INERT_PROPERTY))
