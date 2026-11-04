"""Tests for user-local setup discovery (configs.user_setups_dir & friends).

A microscope setup describes one lab's rig, so real setups are not shipped in
the package — they live in a per-user directory and are discovered alongside
the built-in (emulated + template) setups. These tests pin that mechanism:
resolution of the user dir, the built-in∪user listing, user-shadows-built-in,
and the ``save_setup`` write/backup round-trip.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from PycroFlow import configs


class TestUserSetupsDir(unittest.TestCase):
    """Resolution order of :func:`configs.user_setups_dir`."""

    def test_explicit_env_var_used_verbatim(self):
        with mock.patch.dict(
            os.environ, {"PYCROFLOW_SETUPS_DIR": "/some/where/setups"}
        ):
            self.assertEqual(
                configs.user_setups_dir(), Path("/some/where/setups")
            )

    def test_pycroflow_home_appends_setups(self):
        env = {"PYCROFLOW_HOME": "/opt/pf-home"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("PYCROFLOW_SETUPS_DIR", None)
            self.assertEqual(
                configs.user_setups_dir(), Path("/opt/pf-home/setups")
            )

    def test_posix_default_honours_xdg(self):
        env = {"XDG_CONFIG_HOME": "/home/u/.config"}
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("PYCROFLOW_SETUPS_DIR", "PYCROFLOW_HOME"):
                os.environ.pop(k, None)
            with mock.patch.object(os, "name", "posix"):
                self.assertEqual(
                    configs.user_setups_dir(),
                    Path("/home/u/.config/PycroFlow/setups"),
                )

    def test_default_lands_under_pycroflow_setups(self):
        # The platform default (no env overrides) ends in <...>/PycroFlow/setups
        # on every OS; the Windows LOCALAPPDATA branch is covered on the
        # Windows CI tier (Path can't instantiate a WindowsPath on POSIX).
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("PYCROFLOW_SETUPS_DIR", "PYCROFLOW_HOME"):
                os.environ.pop(k, None)
            got = configs.user_setups_dir()
        self.assertEqual(got.name, "setups")
        self.assertEqual(got.parent.name, "PycroFlow")


class TestSetupDiscovery(unittest.TestCase):
    """list/load/save against a temporary user dir."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pf-setups-")
        self.addCleanup(
            __import__("shutil").rmtree, self._tmp, ignore_errors=True
        )
        patcher = mock.patch.dict(
            os.environ, {"PYCROFLOW_SETUPS_DIR": self._tmp}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name, data):
        with open(Path(self._tmp) / f"{name}.yaml", "w") as f:
            yaml.safe_dump(data, f)

    def test_list_unions_builtin_and_user(self):
        # Built-ins always present; a user setup shows up alongside them.
        self._write("MyRig", {"setup": "MyRig", "emulated": False})
        names = configs.list_setups()
        self.assertIn("Emulator", names)  # shipped built-in
        self.assertIn("MyRig", names)  # user-local
        self.assertEqual(names, sorted(names))  # sorted, de-duped

    def test_user_shadows_builtin_of_same_name(self):
        # A user Emulator.yaml is loaded in preference to the built-in.
        self._write("Emulator", {"setup": "Emulator", "marker": "user-copy"})
        self.assertEqual(
            configs._setup_file("Emulator"),
            Path(self._tmp) / "Emulator.yaml",
        )
        # And listed only once despite existing in both locations.
        self.assertEqual(configs.list_setups().count("Emulator"), 1)

    def test_explicit_path_bypasses_discovery(self):
        p = Path(self._tmp) / "elsewhere.yaml"
        self.assertEqual(configs._setup_file(str(p)), p)

    def test_save_setup_roundtrip_and_backup(self):
        data = {"setup": "Saved", "emulated": False, "n": 1}
        dest = configs.save_setup("Saved", data)
        self.assertEqual(dest, Path(self._tmp) / "Saved.yaml")
        self.assertIn("Saved", configs.list_setups())
        with open(dest) as f:
            self.assertEqual(yaml.safe_load(f)["n"], 1)
        # A second save backs up the previous content to <name>.yaml.bak.
        configs.save_setup("Saved", {"setup": "Saved", "n": 2})
        bak = Path(self._tmp) / "Saved.yaml.bak"
        self.assertTrue(bak.is_file())
        with open(bak) as f:
            self.assertEqual(yaml.safe_load(f)["n"], 1)

    def test_save_setup_creates_missing_dir(self):
        nested = os.path.join(self._tmp, "deep", "nested")
        with mock.patch.dict(os.environ, {"PYCROFLOW_SETUPS_DIR": nested}):
            dest = configs.save_setup("X", {"setup": "X"})
            self.assertTrue(dest.is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
