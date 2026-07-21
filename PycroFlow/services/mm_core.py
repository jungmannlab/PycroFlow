"""Single owner of the Micro-Manager ``Core`` / ``Studio`` connection.

Replaces :class:`PycroFlow.util.PyMgrSingleton`. The connection is held
on a module-level pair of Optionals so:

  * the Qt GUI (Stage 5) can patch monet's ``pycrocore`` global to the
    same instance via ``monet.beampath.pycrocore = get_core()`` before
    instantiating ``MonetMainWindow``, sharing one Core inside one
    process and eliminating the MM connection conflict structurally;
  * tests can call :func:`reset_core` to drop the cached objects and
    re-enter the import-time mock.

Lazy initialization keeps the module importable without ``pycromanager``
on dev / CI machines.
"""

from __future__ import annotations

_core: object | None = None
_studio: object | None = None


def get_core():
    """Return the singleton Micro-Manager Core (lazily created)."""
    global _core
    if _core is None:
        from pycromanager import Core

        _core = Core()
    return _core


def get_studio():
    """Return the singleton Micro-Manager Studio (lazily created)."""
    global _studio
    if _studio is None:
        from pycromanager import Studio

        _studio = Studio(convert_camel_case=True)
    return _studio


def reset_core():
    """Drop the cached Core / Studio. Mostly useful in tests."""
    global _core, _studio
    _core = None
    _studio = None


def is_initialized() -> bool:
    """True iff either Core or Studio has been created."""
    return _core is not None or _studio is not None


def share_with_monet():
    """Patch monet's module-level pycrocore so monet reuses PycroFlow's Core.

    Stage-5 helper. Called by the in-process Qt GUI startup before
    constructing ``MonetMainWindow`` so the two packages share one Core
    inside one process. Safe no-op when monet isn't importable.
    """
    try:
        import monet.beampath as mbp
    except ImportError:
        return
    mbp.pycrocore = get_core()
