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


def chdir_to_test_output():
    """Point the cwd at the per-session output tempdir.

    For tearDown in tests that load an experiment design from the source
    tree: loading chdirs to the design's folder (intended product
    behaviour), which would otherwise leave later tests writing into the
    checkout.

    NOTE this must never run at import time — ``SystemService.load_setup``
    imports this package for emulated setups, so an import-time chdir would
    move the *application's* working directory out from under it.
    """
    os.chdir(TEST_OUTPUT_DIR)


def chdir_to_test_output():
    """Point the cwd at the per-session output tempdir.

    For tearDown in tests that load an experiment design from the source
    tree: loading chdirs to the design's folder (intended product
    behaviour), which would otherwise leave later tests writing into the
    checkout.

    NOTE this must never run at import time — ``SystemService.load_setup``
    imports this package for emulated setups, so an import-time chdir would
    move the *application's* working directory out from under it.
    """
    os.chdir(TEST_OUTPUT_DIR)


@atexit.register
def _cleanup_test_output_dir():
    shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)
