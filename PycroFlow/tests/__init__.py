"""Test package initialization.

Exposes:
    TEST_FIXTURES_DIR: the read-only fixtures directory (PycroFlow/TestData)
        committed to git. Tests load YAML fixtures from here.
    TEST_OUTPUT_DIR: a per-session temporary directory for test outputs
        (acquisitions, save_dir). Cleaned up at interpreter exit.

Previously this module ran ``shutil.rmtree('PycroFlow//TestData')`` at import
time, which silently destroyed the committed fixtures. That destructive
behavior is gone; tests now write into a tempdir instead.

usage:

$ cd /Users/hgrabmayr/GitHub/PycroFlow
$ python -m unittest -v
"""

import atexit
import os
import shutil
import tempfile

# Install vendor-SDK mocks BEFORE any test module is discovered/imported, so
# that ``import PycroFlow.imaging`` etc. succeed in environments without
# pycromanager / monet / pycobolt / nidaqmx installed. No-op when the real
# library is importable.
from PycroFlow.tests._mock_hardware import install_hardware_mocks

install_hardware_mocks()


TEST_FIXTURES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "TestData")
)

TEST_OUTPUT_DIR = tempfile.mkdtemp(prefix="pycroflow-test-")


@atexit.register
def _cleanup_test_output_dir():
    shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)
