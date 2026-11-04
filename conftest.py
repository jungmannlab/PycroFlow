"""Top-level pytest conftest.

pytest auto-loads this; ``python -m unittest discover`` does not. The same
mocks are also installed from ``PycroFlow/tests/__init__.py`` so unittest
discovery works too — keep both paths in sync.
"""

# Importing this submodule runs PycroFlow/tests/__init__.py first, which (a)
# installs the hardware mocks and (b) points PYCROFLOW_SETUPS_DIR at the
# relocated real-rig fixtures — so both are already in effect here; the
# install_hardware_mocks() call below is just the belt-and-braces repeat.
from PycroFlow.tests._mock_hardware import install_hardware_mocks

install_hardware_mocks()
