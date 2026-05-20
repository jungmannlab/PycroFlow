"""Top-level pytest conftest.

pytest auto-loads this; ``python -m unittest discover`` does not. The same
mocks are also installed from ``PycroFlow/tests/__init__.py`` so unittest
discovery works too — keep both paths in sync.
"""
from PycroFlow.tests._mock_hardware import install_hardware_mocks

install_hardware_mocks()
