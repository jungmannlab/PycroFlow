"""Hermetic tests for the headless setup-wizard logic (PycroFlow.setup_core).

No Qt, no instruments: env read/write, first-run detection, template cloning,
validation, and the best-effort connectivity probes (monet / registry) with
injected fakes.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PycroFlow import configs, setup_core


class _TmpEnv(unittest.TestCase):
    """Base: isolate the user setups dir and PYCROFLOW_HOME in a tempdir."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pf-setupcore-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        env = {
            "PYCROFLOW_SETUPS_DIR": os.path.join(self._tmp, "setups"),
            "PYCROFLOW_HOME": self._tmp,
        }
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)


class TestEnvPathAndFirstRun(_TmpEnv):
    def test_env_path_uses_pycroflow_home(self):
        self.assertEqual(setup_core.env_path(), Path(self._tmp) / ".env")

    def test_first_run_true_when_nothing_configured(self):
        self.assertTrue(setup_core.first_run())

    def test_first_run_false_with_env(self):
        setup_core.write_env({setup_core.ENV_SPILL_PORT: "COM5"})
        self.assertFalse(setup_core.first_run())

    def test_first_run_false_with_a_user_setup(self):
        configs.save_setup("MyRig", {"setup": "MyRig", "emulated": False})
        self.assertFalse(setup_core.first_run())


class TestEnvReadWrite(_TmpEnv):
    def test_write_then_read_roundtrip(self):
        setup_core.write_env(
            {
                setup_core.ENV_REGISTRY_URL: "http://reg:8000",
                setup_core.ENV_REGISTRY_TOKEN: "secret-tok",
            }
        )
        got = setup_core.read_env()
        self.assertEqual(got[setup_core.ENV_REGISTRY_URL], "http://reg:8000")
        self.assertEqual(got[setup_core.ENV_REGISTRY_TOKEN], "secret-tok")

    def test_update_in_place_preserves_comments_and_other_keys(self):
        ep = setup_core.env_path()
        ep.write_text(
            "# a comment\nPAINT_REGISTRY_URL=old\nPYCROFLOW_SPILL_PORT=COM1\n",
            encoding="utf-8",
        )
        setup_core.write_env({setup_core.ENV_REGISTRY_URL: "new"})
        body = ep.read_text(encoding="utf-8")
        self.assertIn("# a comment", body)
        self.assertIn("PAINT_REGISTRY_URL=new", body)
        self.assertNotIn("PAINT_REGISTRY_URL=old", body)
        self.assertIn("PYCROFLOW_SPILL_PORT=COM1", body)  # untouched

    def test_write_env_collapses_duplicate_keys(self):
        # A (malformed) .env with a repeated key: the update rewrites the first
        # and the stale later duplicate is dropped, so the value can't be
        # shadowed by the dupe on read.
        ep = setup_core.env_path()
        ep.write_text(
            "PAINT_REGISTRY_URL=old1\nFOO=bar\nPAINT_REGISTRY_URL=old2\n",
            encoding="utf-8",
        )
        setup_core.write_env({setup_core.ENV_REGISTRY_URL: "new"})
        body = ep.read_text(encoding="utf-8")
        self.assertEqual(body.count("PAINT_REGISTRY_URL="), 1)
        self.assertIn("PAINT_REGISTRY_URL=new", body)
        self.assertNotIn("old2", body)
        self.assertIn("FOO=bar", body)  # unrelated key untouched

    def test_written_file_is_private(self):
        ep = setup_core.write_env({setup_core.ENV_SPILL_PORT: "COM9"})
        mode = stat.S_IMODE(os.stat(ep).st_mode)
        # Owner-only on POSIX; Windows ignores the group/other bits.
        if os.name != "nt":
            self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)


class TestCloneAndValidate(_TmpEnv):
    def test_clone_template_overrides_and_saves(self):
        data = setup_core.clone_setup(
            "Template",
            "MyScope",
            overrides={
                "fluid.pumps.interface.COM": "7",
                "illumination.config": "MyScopeMonet",
            },
        )
        self.assertEqual(data["setup"], "MyScope")
        self.assertEqual(data["fluid"]["pumps"]["interface"]["COM"], "7")
        self.assertEqual(data["illumination"]["config"], "MyScopeMonet")
        # Round-trips through save_setup -> load_setup (normalised).
        setup_core.save_setup("MyScope", data)
        self.assertIn("MyScope", configs.list_setups())
        loaded = configs.load_setup("MyScope")
        self.assertEqual(loaded["setup"], "MyScope")

    def test_clone_does_not_mutate_source(self):
        a = setup_core.clone_setup("Template", "A", {"setup": "A"})
        b = setup_core.clone_setup("Template", "B")
        self.assertEqual(b["setup"], "B")
        self.assertNotEqual(a["fluid"], None)

    def test_apply_override_none_is_noop(self):
        data = setup_core.clone_setup(
            "Template", "X", {"fluid.pumps.interface.COM": None}
        )
        # The template's placeholder COM is left untouched by a None override.
        self.assertEqual(data["fluid"]["pumps"]["interface"]["COM"], "0")

    def test_validate_flags_missing_name_and_com(self):
        self.assertIn(
            "setup name is empty", setup_core.validate_setup({"fluid": {}})
        )
        probs = setup_core.validate_setup(
            {"setup": "R", "emulated": False, "fluid": {"pumps": {}}}
        )
        self.assertTrue(any("COM" in p for p in probs))

    def test_validate_emulated_needs_no_com(self):
        self.assertEqual(
            setup_core.validate_setup(
                {"setup": "E", "emulated": True, "fluid": {}}
            ),
            [],
        )


class TestConnectivityProbes(_TmpEnv):
    def test_registry_unset_is_not_configured(self):
        v = setup_core.check_registry("")
        self.assertFalse(v["ok"])
        self.assertIn("not set", v["detail"])

    def test_registry_reachable_and_unreachable(self):
        calls = {}

        class _Resp:
            status_code = 200

        def _get(url, headers=None, timeout=None):
            calls["url"] = url
            calls["headers"] = headers
            return _Resp()

        fake = types.SimpleNamespace(get=_get)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            v = setup_core.check_registry("http://reg:8000/", token="t")
        self.assertTrue(v["ok"])
        self.assertEqual(calls["url"], "http://reg:8000/health")
        self.assertEqual(calls["headers"], {"Authorization": "Bearer t"})

        def _boom(*a, **k):
            raise OSError("refused")

        fake_bad = types.SimpleNamespace(get=_boom)
        with mock.patch.dict(sys.modules, {"requests": fake_bad}):
            v2 = setup_core.check_registry("http://reg:8000")
        self.assertFalse(v2["ok"])
        self.assertIn("unreachable", v2["detail"])

    def test_monet_config_present_and_absent(self):
        fake_monet = types.SimpleNamespace(CONFIGS={"Scope": {}})
        with mock.patch.dict(sys.modules, {"monet": fake_monet}):
            self.assertTrue(setup_core.check_monet_config("Scope")["ok"])
            self.assertFalse(setup_core.check_monet_config("Nope")["ok"])
        self.assertFalse(setup_core.check_monet_config("")["ok"])

    def test_enumerate_serial_ports_returns_list(self):
        self.assertIsInstance(setup_core.enumerate_serial_ports(), list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
