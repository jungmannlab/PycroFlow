"""Regression: setup_logging must survive a windowed frozen build.

A ``console=False`` PyInstaller exe double-clicked with no console has
``sys.stderr is None``; loguru rejects a None sink, which crashed the GUI at
startup (`Cannot log to objects of type 'NoneType'`). The stderr sink must be
skipped in that case while the file sinks still work.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import PycroFlow
from loguru import logger


class TestSetupLoggingHeadless(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pf-log-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        # Leave loguru clean for whatever runs next.
        self.addCleanup(logger.remove)

    def test_setup_logging_with_stderr_none_does_not_crash(self):
        logfile = os.path.join(self._tmp, "pycroflow.log")
        ham = os.path.join(self._tmp, "hamilton.log")
        with mock.patch.object(sys, "stderr", None):
            # Must not raise even though the stderr sink can't be added.
            PycroFlow.setup_logging(logfile=logfile, hamilton_logfile=ham)
        logger.info("headless log line")  # exercise the file sink
        self.assertTrue(os.path.exists(logfile))

    def test_setup_logging_adds_stderr_when_present(self):
        # The normal path still installs the stderr sink.
        logfile = os.path.join(self._tmp, "p.log")
        PycroFlow.setup_logging(logfile=logfile, hamilton_logfile=None)
        self.assertIsNotNone(sys.stderr)  # sanity: a real stream here
        self.assertTrue(PycroFlow.logging_configured())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
