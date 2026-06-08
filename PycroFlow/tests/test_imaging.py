import os
import unittest
from unittest.mock import MagicMock, patch
import logging

import PycroFlow.imaging as pim
from PycroFlow.tests import TEST_OUTPUT_DIR


logger = logging.getLogger(__name__)


def _make_config():
    return {
        'save_dir': TEST_OUTPUT_DIR,
        'base_name': 'AutomationTest_R2R4',
        # ImagingSystem.__init__ seeds a PFS log from these tags via the
        # (mocked) Core; values are irrelevant, only the keys must exist.
        'pfs_pars': {
            'tag_zdrive': 'ZDrive',
            'tag_status': 'PFS',
            'prop_status': 'PFS Status',
            'prop_state': 'PFS in Range',
        },
    }


def _make_protocol():
    return {
        'parameters': {
            'show_progress': False,
            'show_display': True,
            'close_display_after_acquisition': True,
        },
        'protocol_entries': [
            {'$type': 'acquire', 'frames': 10, 't_exp': 100,
             'message': 'round_1'},
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
        self.mock_acquisition = patch(
            'PycroFlow.imaging.Acquisition').start()
        self.addCleanup(patch.stopall)
        patch('PycroFlow.imaging.multi_d_acquisition_events',
              return_value=[None]).start()

        # Avoid touching a real Micro-Manager: hand back mock Core/Studio and
        # neutralize the filesystem MM-Core lock.
        self.mock_core = MagicMock(name='core')
        self.mock_studio = MagicMock(name='studio')
        patch('PycroFlow.services.mm_core.get_core',
              return_value=self.mock_core).start()
        patch('PycroFlow.services.mm_core.get_studio',
              return_value=self.mock_studio).start()
        patch('PycroFlow.imaging.MmCoreLock').start()

    def test_01_construction(self):
        """ImagingSystem constructs and runs its self-test acquisition."""
        isy = pim.ImagingSystem(_make_config())
        isy._assign_protocol(_make_protocol())
        # __init__ runs test_acquisition(), which opens one Acquisition.
        self.assertTrue(self.mock_acquisition.called)
        self.assertTrue(os.path.isdir(isy.config['save_dir']))

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
            isy.config['save_dir'], 'prtclstep0_round_1_pfs.xlsx')
        self.assertTrue(os.path.isfile(pfs_log))


if __name__ == '__main__':
    unittest.main()
