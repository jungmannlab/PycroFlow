"""Tests for the frontends' best-effort .env loading (envfile.py)."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

from PycroFlow.envfile import load_env_file

_HAVE_DOTENV = importlib.util.find_spec("dotenv") is not None


class TestLoadEnvFile(unittest.TestCase):

    def test_missing_file_is_a_quiet_noop(self):
        self.assertFalse(load_env_file("/nonexistent/.env"))

    def test_repo_root_fallback_path_sits_next_to_the_template(self):
        """The cwd-independent fallback points at the repo root (where
        .env.template lives), so a GUI launched from an unrelated directory
        still finds the repo's .env on an editable install."""
        from PycroFlow.envfile import repo_root_env_path

        root = os.path.dirname(repo_root_env_path())
        self.assertTrue(
            os.path.exists(os.path.join(root, "pyproject.toml"))
            or os.path.exists(os.path.join(root, ".env.template")),
            "fallback does not point at the repo root: {}".format(root),
        )

    @unittest.skipUnless(_HAVE_DOTENV, "python-dotenv not installed")
    def test_loads_values_without_overriding_exported_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w") as f:
                f.write(
                    "PYCROFLOW_TEST_ENVFILE=from_file\n"
                    "PYCROFLOW_TEST_ENVFILE_SET=from_file\n"
                )
            os.environ["PYCROFLOW_TEST_ENVFILE_SET"] = "from_shell"
            try:
                self.assertTrue(load_env_file(path))
                self.assertEqual(
                    os.environ["PYCROFLOW_TEST_ENVFILE"], "from_file"
                )
                # override=False: the exported variable wins.
                self.assertEqual(
                    os.environ["PYCROFLOW_TEST_ENVFILE_SET"], "from_shell"
                )
            finally:
                os.environ.pop("PYCROFLOW_TEST_ENVFILE", None)
                os.environ.pop("PYCROFLOW_TEST_ENVFILE_SET", None)

    def test_template_covers_the_wired_env_vars(self):
        """The tracked .env.template documents every PAINT_/PYCROFLOW_ env
        var the code reads, so a new variable can't silently miss it."""
        import PycroFlow

        root = os.path.dirname(os.path.dirname(PycroFlow.__file__))
        template = os.path.join(root, ".env.template")
        if not os.path.exists(template):
            self.skipTest("installed without the repo root (no template)")
        with open(template) as f:
            body = f.read()
        for var in (
            "PAINT_REGISTRY_URL",
            "PAINT_REGISTRY_TOKEN",
            "PYCROFLOW_REGISTRY_BUFFER",
            "PYCROFLOW_SPILL_PORT",
            "PYCROFLOW_LIVE_ANALYSIS",
            "MONET_CONFIG_PATHS",
            "PAINT_MONET_TOKEN",
        ):
            self.assertIn(var, body)


if __name__ == "__main__":
    unittest.main()
