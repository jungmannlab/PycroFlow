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
    'pycromanager',
    'pycromanager.acquisitions',
    'pycromanager.acq_util',
    'pycromanager.zmq_bridge',
    'monet',
    'monet.control',
    'monet.gui',
    'monet.beampath',
    'pycobolt',
    'nidaqmx',
    'ThorlabsPM100',
    'pyvisa',
    'msl',
    'msl.equipment',
    'Arduino',
    'pandas',
    'lmfit',
    'matplotlib',
    'matplotlib.pyplot',
    'PyHamiltonPSD',
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
