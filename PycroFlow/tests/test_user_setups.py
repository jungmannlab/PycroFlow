"""Tests for user-local setup discovery (configs.user_setups_dir & friends).

A microscope setup describes one lab's rig, so real setups are not shipped in
the package — they live in a per-user directory and are discovered alongside
the built-in (emulated + template) setups. These tests pin that mechanism:
resolution of the user dir, the built-in∪user listing, user-shadows-built-in,
and the ``save_setup`` write/backup round-trip.
"""

from __future__ import annotations

import os
import shutil
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
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
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

    def test_list_builtins_only_when_user_dir_absent(self):
        # Fresh install: the user dir does not exist yet -> built-ins only,
        # no crash (list_setups guards on is_dir()).
        missing = os.path.join(self._tmp, "does-not-exist")
        with mock.patch.dict(os.environ, {"PYCROFLOW_SETUPS_DIR": missing}):
            names = configs.list_setups()
        self.assertIn("Emulator", names)
        self.assertFalse(Path(missing).exists())

    def test_user_shadows_builtin_at_load_level(self):
        # A user Emulator.yaml wins over the built-in — verified by reading the
        # raw content back (not just the resolved path), so a load-path
        # regression can't pass silently.
        self._write("Emulator", {"setup": "Emulator", "marker": "user-copy"})
        self.assertEqual(
            configs._setup_file("Emulator"),
            Path(self._tmp) / "Emulator.yaml",
        )
        self.assertEqual(
            configs.load_setup_file("Emulator")["marker"], "user-copy"
        )
        # And listed only once despite existing in both locations.
        self.assertEqual(configs.list_setups().count("Emulator"), 1)

    def test_dotted_bare_name_resolves_to_yaml(self):
        # A version-tagged name ('Mercury.v2') is a bare name, not a path —
        # it must resolve to <user>/Mercury.v2.yaml, not be opened literally.
        self._write("Mercury.v2", {"setup": "Mercury.v2"})
        self.assertEqual(
            configs._setup_file("Mercury.v2"),
            Path(self._tmp) / "Mercury.v2.yaml",
        )
        self.assertEqual(
            configs.load_setup_file("Mercury.v2")["setup"], "Mercury.v2"
        )

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

    def test_clone_edit_save_roundtrip_with_tubing(self):
        # The clone→edit→save path the wizard uses: load_setup_file (raw, with
        # tubing as records) → edit → save_setup → load_setup (normalised,
        # tubing as a tuple-keyed dict). Proves save_setup serialises a real
        # setup shape and the saved file is loadable.
        src = configs.load_setup_file("Emulator")  # a shipped built-in (raw)
        src["setup"] = "ClonedRig"
        dest = configs.save_setup("ClonedRig", src)
        self.assertTrue(dest.is_file())
        loaded = configs.load_setup("ClonedRig")  # normalised load
        self.assertEqual(loaded["setup"], "ClonedRig")
        tubing = loaded.get("fluid", {}).get("tubing")
        if tubing:  # Emulator carries tubing -> must normalise to tuple keys
            self.assertTrue(all(isinstance(k, tuple) for k in tubing))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
