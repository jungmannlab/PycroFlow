"""Top-level pytest conftest.

pytest auto-loads this; ``python -m unittest discover`` does not. The same
mocks are also installed from ``PycroFlow/tests/__init__.py`` so unittest
discovery works too — keep both paths in sync.
"""

import os

from PycroFlow.tests._mock_hardware import install_hardware_mocks

install_hardware_mocks()

# Mirror tests/__init__.py: discover the relocated real-rig setups (now test
# fixtures) so the pytest runner resolves `load_setup('Mercury'/'Ibidi')` too.
os.environ.setdefault(
    "PYCROFLOW_SETUPS_DIR",
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "PycroFlow",
            "tests",
            "fixtures",
            "setups",
        )
    ),
)
