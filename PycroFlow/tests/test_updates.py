"""Tests for the GitHub release update-check (PycroFlow.updates).

Pure logic + a mocked requests layer — no network. Mirrors monet's updates
tests: tag normalisation, PEP-440 comparison, the never-nag rule for untagged
builds, and the never-raise verdict dict.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from PycroFlow import updates


class TestVersionLogic(unittest.TestCase):
    def test_normalize_strips_leading_v(self):
        self.assertEqual(updates._normalize("v1.2.3"), "1.2.3")
        self.assertEqual(updates._normalize("1.2.3"), "1.2.3")
        self.assertEqual(updates._normalize("  V0.1  "), "0.1")
        self.assertEqual(updates._normalize(None), "")

    def test_is_newer(self):
        self.assertTrue(updates._is_newer("1.2.0", "1.1.9"))
        self.assertTrue(updates._is_newer("1.10.0", "1.9.0"))  # not string cmp
        self.assertFalse(updates._is_newer("1.1.0", "1.1.0"))  # equal
        self.assertFalse(updates._is_newer("1.0.0", "1.1.0"))  # older

    def test_is_newer_never_nags_non_release(self):
        for cur in ("", "unknown", "0.0.0"):
            self.assertFalse(updates._is_newer("9.9.9", cur))

    def test_is_newer_unparseable_latest(self):
        self.assertFalse(updates._is_newer("not-a-version", "1.0.0"))


class TestLatestRelease(unittest.TestCase):
    def _fake_requests(self, tag):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"tag_name": tag}

        return types.SimpleNamespace(
            get=lambda url, timeout=None, headers=None: _Resp()
        )

    def test_latest_release_normalises(self):
        with mock.patch.dict(
            sys.modules, {"requests": self._fake_requests("v2.3.4")}
        ):
            self.assertEqual(updates.latest_release(), "2.3.4")


class TestCheckForUpdate(unittest.TestCase):
    def test_update_available(self):
        with mock.patch.object(
            updates, "latest_release", return_value="9.9.9"
        ):
            v = updates.check_for_update(current="1.0.0")
        self.assertTrue(v["update_available"])
        self.assertEqual(v["latest"], "9.9.9")
        self.assertEqual(v["current"], "1.0.0")
        self.assertIsNone(v["error"])
        self.assertIn("releases", v["url"])

    def test_up_to_date(self):
        with mock.patch.object(
            updates, "latest_release", return_value="1.0.0"
        ):
            v = updates.check_for_update(current="1.0.0")
        self.assertFalse(v["update_available"])
        self.assertIsNone(v["error"])

    def test_network_error_is_reported_not_raised(self):
        def _boom(timeout=10):
            raise OSError("offline")

        with mock.patch.object(updates, "latest_release", _boom):
            v = updates.check_for_update(current="1.0.0")
        self.assertIsNone(v["latest"])
        self.assertFalse(v["update_available"])
        self.assertIn("offline", v["error"])

    def test_source_tree_never_nags(self):
        with mock.patch.object(
            updates, "latest_release", return_value="9.9.9"
        ):
            v = updates.check_for_update(current="0.0.0")
        self.assertFalse(v["update_available"])

    def test_default_current_is_package_version(self):
        import PycroFlow

        with mock.patch.object(
            updates, "latest_release", return_value="0.0.1"
        ):
            v = updates.check_for_update()
        self.assertEqual(v["current"], PycroFlow.__version__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
