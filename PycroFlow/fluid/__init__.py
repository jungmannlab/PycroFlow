"""Fluid-handling subsystem.

The Hamilton fluid stack — previously a 2.7k-line flat
``hamilton_architecture.py`` — lives here as of Stage 3. The class itself
moved to :mod:`PycroFlow.fluid.legacy` whole; sibling modules
(``calibration``, ``protocol_exec``, ``wet_tests``) are placeholders for
the follow-up extraction that the plan calls for.

Back-compat: ``import PycroFlow.hamilton_architecture as ha`` still works
via the shim at ``PycroFlow/hamilton_architecture.py``.
"""

from PycroFlow.fluid.legacy import LegacyArchitecture

__all__ = ["LegacyArchitecture"]
