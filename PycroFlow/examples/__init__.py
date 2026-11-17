"""Runnable examples and demo data for PycroFlow.

Besides the REPL demo protocols, this subpackage ships a few example
high-level experiment designs (``*.yaml``) that the GUI's "New from example…"
action offers as starting points — see :func:`design_examples`.
"""

from __future__ import annotations

import os

#: Directory holding the shipped example experiment-design YAMLs.
_HERE = os.path.dirname(os.path.abspath(__file__))


def design_examples() -> list:
    """Return the shipped example experiment designs as ``(label, path)``.

    Every ``*.yaml`` next to this module is an example design; the label is a
    human-friendly name derived from the filename. Sorted by label so the GUI
    picker order is stable.
    """
    out = []
    for name in os.listdir(_HERE):
        if not name.lower().endswith((".yaml", ".yml")):
            continue
        label = os.path.splitext(name)[0].replace("_", " ")
        out.append((label, os.path.join(_HERE, name)))
    return sorted(out, key=lambda pair: pair[0])
