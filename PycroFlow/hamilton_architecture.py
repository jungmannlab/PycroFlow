"""Back-compat re-export shim for ``PycroFlow.fluid.legacy``.

The Hamilton fluid architecture moved from this flat module into the
:mod:`PycroFlow.fluid` subpackage during Stage 3 of the restructuring.
Every public name previously importable from
``PycroFlow.hamilton_architecture`` is still importable here — existing
call sites (``frontend_cli.py``, ``example_experiment/*.py``,
``prep_legacy_wettest()``, the rest of the package) continue to work
unchanged.

New code should prefer the canonical location::

    from PycroFlow.fluid.legacy import LegacyArchitecture, connect

Stage-4 follow-up may split ``fluid/legacy.py`` further (calibration,
protocol-entry execution, wet tests) into sibling submodules. This shim
will continue to re-export the union for back-compat.
"""

from PycroFlow.fluid.legacy import *  # noqa: F401, F403

# Explicit re-exports for the most commonly-referenced names, so that
# static analysis (and ``from PycroFlow.hamilton_architecture import X``
# at import time) does not have to rely solely on the star-import.
from PycroFlow.fluid.legacy import (  # noqa: F401
    LegacyArchitecture,
    connect,
    disconnect,
    is_connected,
    legacy_system_config,
    legacy_tubing_config,
    prep_legacy_wettest,
    do_legacy_wettest,
    find_reservoirs,
)
