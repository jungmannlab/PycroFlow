"""Tests for PycroFlow.util (time formatting, progress bar, MM singleton)."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from PycroFlow import util


class FmtTimeDeltaTest(unittest.TestCase):
    def test_zero_is_blank_padded_to_width(self):
        out = util.fmt_time_delta(0, width=10)
        self.assertEqual(out, " " * 10)

    def test_contains_unit_snippets(self):
        out = util.fmt_time_delta(65)
        self.assertIn("min", out)
        self.assertIn("s", out)

    def test_truncated_to_width(self):
        # A large delta produces a long string that must be clipped to width.
        out = util.fmt_time_delta(100000, width=5)
        self.assertEqual(len(out), 5)

    def test_always_padded_to_width(self):
        self.assertEqual(len(util.fmt_time_delta(65, width=30)), 30)


class ProgressBarTest(unittest.TestCase):
    def test_progress_and_end_do_not_raise(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            pb = util.ProgressBar("Acq", 10)
            pb.progress(0.5)
            pb.progress(1)  # the x==1 branch (chardeci becomes '')
            pb.end_progress()
        # The title is printed on construction and at the end.
        self.assertIn("Acq", buf.getvalue())

    def test_progress_increment_counts(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            pb = util.ProgressBar("Acq", 4)
            pb.progress_increment()
            pb.progress_increment()
        self.assertEqual(pb.nimgs_acquired, 2)


class PyMgrSingletonTest(unittest.TestCase):
    def setUp(self):
        # Class-level caches persist across the process; clear them so the
        # caching assertions are meaningful regardless of test order.
        util.PyMgrSingleton._PyMgrSingleton__core = None
        util.PyMgrSingleton._PyMgrSingleton__studio = None
        util.PyMgrSingleton._PyMgrSingleton__instance = None

    def tearDown(self):
        util.PyMgrSingleton._PyMgrSingleton__core = None
        util.PyMgrSingleton._PyMgrSingleton__studio = None
        util.PyMgrSingleton._PyMgrSingleton__instance = None

    def test_get_core_caches_single_instance(self):
        with patch(
            "PycroFlow.util.Core", return_value=MagicMock(name="Core")
        ) as core_cls:
            a = util.PyMgrSingleton.get_core()
            b = util.PyMgrSingleton.get_core()
        self.assertIs(a, b)
        core_cls.assert_called_once()

    def test_get_studio_caches_single_instance(self):
        with patch(
            "PycroFlow.util.Studio", return_value=MagicMock(name="Studio")
        ) as studio_cls:
            a = util.PyMgrSingleton.get_studio()
            b = util.PyMgrSingleton.get_studio()
        self.assertIs(a, b)
        studio_cls.assert_called_once_with(convert_camel_case=True)

    def test_direct_second_instantiation_rejected(self):
        util.PyMgrSingleton._PyMgrSingleton__instance = object()
        with self.assertRaises(Exception):
            util.PyMgrSingleton()


if __name__ == "__main__":
    unittest.main()
