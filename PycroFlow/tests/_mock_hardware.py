"""Install ``sys.modules`` mocks for vendor SDKs that may be missing in CI/dev.

The lab Windows box has the real pycromanager / monet / pycobolt / nidaqmx
installed; on CI runners (and developer macOS machines) these are not
available. We install lightweight ``unittest.mock.MagicMock`` shims for any
missing module so that test discovery — which transitively imports
``PycroFlow.imaging``, ``PycroFlow.illumination``, etc. — does not crash.

Real installations win: mocks are only inserted for modules that fail to
import.

Hardware-specific behavior must therefore NOT be tested through these mocks;
keep hardware integration tests gated on real-SDK availability with
``unittest.skipUnless``.
"""

import importlib
import sys
from unittest.mock import MagicMock

_HARDWARE_MODULES = [
    # pycromanager 1.0 (B8/C41 numpy-2 harmonization): the submodule layout
    # changed from the 0.29 era. The old shims (pycromanager.acquisitions /
    # .acq_util / .zmq_bridge) no longer exist; 1.0 exposes .acquisition,
    # .mm_java_classes and the split-out pyjavaz / ndstorage packages. No
    # PycroFlow module imports these submodules directly (all use top-level
    # `from pycromanager import ...`), so mocking the top-level name is what
    # actually matters; the submodules are listed only to keep the shim honest.
    "pycromanager",
    "pycromanager.acquisition",
    "pycromanager.mm_java_classes",
    "pyjavaz",
    "ndstorage",
    "monet",
    "monet.control",
    "monet.gui",
    "monet.beampath",
    "pycobolt",
    "nidaqmx",
    "ThorlabsPM100",
    "pyvisa",
    "msl",
    "msl.equipment",
    "Arduino",
    "pandas",
    "lmfit",
    "matplotlib",
    "matplotlib.pyplot",
    "PyHamiltonPSD",
]


def _import_succeeds(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def install_hardware_mocks():
    """Insert MagicMock entries into ``sys.modules`` for missing vendor libs."""
    for name in _HARDWARE_MODULES:
        if name in sys.modules:
            continue
        if _import_succeeds(name):
            continue
        sys.modules[name] = MagicMock(name=name)
