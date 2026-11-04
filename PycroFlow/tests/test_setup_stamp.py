"""Tests for registry setup stamping (setup_acquisition_fields).

Content-addresses the active microscope setup onto the acquisition_run record
(microscope_id + full config_yaml + a SHA-256 "version"), best-effort.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PycroFlow import configs
from PycroFlow.live_analysis.registry_payload import (
    build_fov_payload,
    setup_acquisition_fields,
)


class TestSetupAcquisitionFields(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pf-stamp-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"PYCROFLOW_SETUPS_DIR": self._tmp})
        p.start()
        self.addCleanup(p.stop)

    def test_empty_or_unknown_name_is_noop(self):
        self.assertEqual(setup_acquisition_fields(None), {})
        self.assertEqual(setup_acquisition_fields(""), {})
        self.assertEqual(setup_acquisition_fields("NoSuchRig"), {})

    def test_stamps_name_yaml_and_hash(self):
        configs.save_setup("Rig", {"setup": "Rig", "emulated": False})
        text = Path(configs.setup_path("Rig")).read_text(encoding="utf-8")
        fields = setup_acquisition_fields("Rig")
        self.assertEqual(fields["microscope_id"], "Rig")
        self.assertEqual(fields["config_yaml"], text)
        self.assertEqual(
            fields["extra"]["setup_sha256"],
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def test_hash_changes_with_content(self):
        configs.save_setup("Rig", {"setup": "Rig", "n": 1})
        h1 = setup_acquisition_fields("Rig")["extra"]["setup_sha256"]
        configs.save_setup("Rig", {"setup": "Rig", "n": 2})
        h2 = setup_acquisition_fields("Rig")["extra"]["setup_sha256"]
        self.assertNotEqual(h1, h2)

    def test_fields_flow_into_acquisition_run_payload(self):
        configs.save_setup("Rig", {"setup": "Rig"})
        acq = setup_acquisition_fields("Rig")
        payload = build_fov_payload(
            run_id="r1", metrics={"n_locs": 1}, acquisition=acq
        )
        row = payload["acquisition_run"]
        self.assertEqual(row["id"], "r1")  # not clobbered by the merge
        self.assertEqual(row["microscope_id"], "Rig")
        self.assertIn("config_yaml", row)
        self.assertEqual(
            row["extra"]["setup_sha256"], acq["extra"]["setup_sha256"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
