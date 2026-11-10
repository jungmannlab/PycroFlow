import os
import unittest
from unittest.mock import MagicMock, patch
import logging

import PycroFlow.imaging as pim
from PycroFlow.tests import TEST_OUTPUT_DIR

logger = logging.getLogger(__name__)


def _make_config():
    return {
        "save_dir": TEST_OUTPUT_DIR,
        "base_name": "AutomationTest_R2R4",
        # ImagingSystem.__init__ seeds a PFS log from these tags via the
        # (mocked) Core; values are irrelevant, only the keys must exist.
        "pfs_pars": {
            "tag_zdrive": "ZDrive",
            "tag_status": "PFS",
            "prop_status": "PFS Status",
            "prop_state": "PFS in Range",
        },
    }


def _make_protocol():
    return {
        "parameters": {
            "show_progress": False,
            "show_display": True,
            "close_display_after_acquisition": True,
        },
        "protocol_entries": [
            {
                "$type": "acquire",
                "frames": 10,
                "t_exp": 100,
                "message": "round_1",
            },
        ],
    }


class TestImaging(unittest.TestCase):
    """Exercise ImagingSystem with the pycromanager / MM Core surface mocked.

    ``imaging.py`` binds ``Acquisition`` / ``multi_d_acquisition_events`` at
    import time (``from pycromanager import ...``) and pulls Core/Studio from
    ``services.mm_core``. We therefore patch those names *where they are used*
    rather than reaching into ``pycromanager`` submodules (whose layout is a
    vendor detail — the old ``pycromanager.acquisitions`` path no longer
    exists, which is what made this test error in setUp).
    """

    def setUp(self):
        # Acquisition is used as a context manager; MagicMock supports the
        # protocol out of the box (__enter__/__exit__ return MagicMocks).
        self.mock_acquisition = patch("PycroFlow.imaging.Acquisition").start()
        self.addCleanup(patch.stopall)
        patch(
            "PycroFlow.imaging.multi_d_acquisition_events", return_value=[None]
        ).start()

        # Avoid touching a real Micro-Manager: hand back mock Core/Studio and
        # neutralize the filesystem MM-Core lock.
        self.mock_core = MagicMock(name="core")
        self.mock_studio = MagicMock(name="studio")
        patch(
            "PycroFlow.services.mm_core.get_core", return_value=self.mock_core
        ).start()
        patch(
            "PycroFlow.services.mm_core.get_studio",
            return_value=self.mock_studio,
        ).start()
        patch("PycroFlow.imaging.MmCoreLock").start()

    def test_01_construction(self):
        """ImagingSystem constructs + self-tests WITHOUT creating a run folder.

        The run folder is resolved + created at run start (prepare_savedir),
        not at connect, so a mere connect no longer writes into save_dir.
        """
        isy = pim.ImagingSystem(_make_config())
        isy._assign_protocol(_make_protocol())
        # __init__ runs test_acquisition(), which opens one Acquisition.
        self.assertTrue(self.mock_acquisition.called)
        # Nothing created at connect; it resolves at run start.
        self.assertFalse(isy._savedir_ready)
        run_dir = isy.prepare_savedir()
        self.assertTrue(os.path.isdir(run_dir))
        self.assertEqual(isy.config["save_dir"], run_dir)

    def test_prepare_savedir_appends_when_folder_exists(self):
        """An existing target folder gets the first free _1/_2 suffix rather
        than erroring — so running into one save_dir twice never collides."""
        import types

        name = "savedir_collision_test"

        def make():
            o = types.SimpleNamespace(
                config={"save_dir": TEST_OUTPUT_DIR, "base_name": name}
            )
            return pim.ImagingSystem.prepare_savedir(o)

        p0, p1, p2 = make(), make(), make()
        self.assertEqual(p0, os.path.join(TEST_OUTPUT_DIR, name))
        self.assertEqual(p1, os.path.join(TEST_OUTPUT_DIR, name + "_1"))
        self.assertEqual(p2, os.path.join(TEST_OUTPUT_DIR, name + "_2"))
        for p in (p0, p1, p2):
            self.assertTrue(os.path.isdir(p))

    def test_prepare_savedir_redirects_to_the_live_design(self):
        """prepare_savedir(save_dir, base_name) writes under the NEW location,
        not the one bound at connect — the stale-save_dir fix."""
        isy = pim.ImagingSystem(_make_config())
        new_root = os.path.join(TEST_OUTPUT_DIR, "redirected_root")
        run_dir = isy.prepare_savedir(new_root, "edited_name")
        self.assertEqual(run_dir, os.path.join(new_root, "edited_name"))
        self.assertTrue(os.path.isdir(run_dir))
        self.assertEqual(isy.config["save_dir"], run_dir)

    def test_02(self):
        """Executing an 'acquire' entry drives a pycromanager Acquisition."""
        isy = pim.ImagingSystem(_make_config())
        isy._assign_protocol(_make_protocol())

        calls_before = self.mock_acquisition.call_count
        isy.execute_protocol_entry(0)

        # The acquire entry opened a further Acquisition and set the exposure
        # to the entry's t_exp.
        self.assertGreater(self.mock_acquisition.call_count, calls_before)
        self.mock_core.set_exposure.assert_any_call(100)
        # A PFS log was written next to the acquisition.
        pfs_log = os.path.join(
            isy.config["save_dir"], "prtclstep0_round_1_pfs.xlsx"
        )
        self.assertTrue(os.path.isfile(pfs_log))

    def test_monitoring_acquire_arms_subtitle_overlay(self):
        """A monitoring acquire entry arms the frame subtitle; a plain one
        does not."""
        isy = pim.ImagingSystem(_make_config())
        proto = _make_protocol()
        proto["protocol_entries"] = [
            {
                "$type": "acquire",
                "frames": 5,
                "t_exp": 100,
                "message": "round_monitor-x",
                "name": "imager1 (wash monitor)",
                "monitoring": True,
            },
            {"$type": "acquire", "frames": 5, "t_exp": 100, "message": "sci"},
        ]
        isy._assign_protocol(proto)
        # Stub the actual recording — we only care about the overlay arming.
        isy.do_all_recodrings = lambda *a, **k: None
        isy.prepare_savedir = lambda *a, **k: None
        isy._savedir_ready = True

        isy.execute_protocol_entry(0)
        self.assertIsNotNone(isy._monitor_overlay)
        self.assertEqual(
            isy._monitor_overlay["label"], "imager1 (wash monitor)"
        )
        # A subsequent scientific acquire disarms it.
        isy.execute_protocol_entry(1)
        self.assertIsNone(isy._monitor_overlay)

    def test_image_process_fn_overlays_monitor_frames_only(self):
        """image_process_fn burns the subtitle into monitor frames and leaves
        scientific frames byte-identical."""
        import time
        import queue
        import numpy as np

        isy = pim.ImagingSystem(_make_config())
        isy._assign_protocol(_make_protocol())
        isy.curr_frame = 1  # skip the every-100th-frame PFS branch
        frame = np.zeros((64, 64), dtype=np.uint16)

        # No overlay armed -> the frame is returned untouched.
        isy._monitor_overlay = None
        out, _ = isy.image_process_fn(frame.copy(), {}, queue.Queue())
        self.assertTrue(np.array_equal(out, frame))

        # Overlay armed -> a subtitle is burned into the bottom of the frame.
        isy._monitor_overlay = {
            "label": "imager1 (wash monitor)",
            "start": time.time(),
        }
        out2, _ = isy.image_process_fn(frame.copy(), {}, queue.Queue())
        self.assertEqual(out2.shape, frame.shape)
        self.assertEqual(out2.dtype, frame.dtype)
        self.assertFalse(np.array_equal(out2, frame))
        self.assertTrue(np.array_equal(out2[:30], frame[:30]))  # top untouched


if __name__ == "__main__":
    unittest.main()
