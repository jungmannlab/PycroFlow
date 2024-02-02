import unittest
from unittest.mock import MagicMock, patch
import logging
import time

import PycroFlow.imaging as pim
from pycromanager import Core, Studio


logger = logging.getLogger(__name__)


class TestImaging(unittest.TestCase):

    def setUp(self):
        patch_acquisition = patch('pycromanager.acquisitions.Acquisition', create=True)
        patch_acquisition.start()
        self.addCleanup(patch_acquisition.stop)
        patch_bridge = patch('pycromanager.acquisitions.Bridge', create=True)
        patch_bridge.start()
        self.addCleanup(patch_bridge.stop)
        patch_mud = patch('pycromanager.acq_util.multi_d_acquisition_events', create=True)
        patch_mud.start()
        self.addCleanup(patch_mud.stop)
        patch_zmq = patch('pycromanager.zmq_bridge', create=True)
        patch_zmq.start()
        self.addCleanup(patch_zmq.stop)
        patch_acqs = patch('pycromanager.acquisitions', create=True)
        patch_acqs.start()
        self.addCleanup(patch_acqs.stop)

    def tearDown(self):
        pass

    def test_01(self):
        try:
            core = Core()
            studio = Studio(convert_camel_case=True)
            studio.live().is_live_mode_on()
            time.sleep(10)
        except:
            core = MagicMock()
            studio = MagicMock()

        imaging_settings = {
            'frames': 10,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }
        flow_acq_config = {
            'save_dir': r'PycroFlow//TestData',
            'base_name': 'AutomationTest_R2R4',
            'imaging_settings': imaging_settings,
            'mm_parameters': {
                'channel_group': 'Filter turret',
                'filter': '2-G561',
            },
        }

        protocol_imaging = [
            {'type': 'acquire', 'frames': 100,
             't_exp': 100, 'message': 'round_1'},
        ]

        try:
            del core
            del studio
            pim.ImagingSystem(
                flow_acq_config, protocol_imaging)
        except:
            print('skipping test as mm is not connected')

    def test_02(self):
        try:
            core = Core()
            studio = Studio(convert_camel_case=True)
            studio.live().is_live_mode_on()
        except:
            core = unittest.mock.Mock()
            studio = unittest.mock.Mock()

        imaging_settings = {
            'frames': 10,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }
        flow_acq_config = {
            'save_dir': r'PycroFlow//TestData',
            'base_name': 'AutomationTest_R2R4',
            'imaging_settings': imaging_settings,
            'mm_parameters': {
                'channel_group': 'Filter turret',
                'filter': '2-G561',
            },
        }

        protocol_imaging = [
            {'type': 'acquire', 'frames': 100,
             't_exp': 100, 'message': 'round_1'},
        ]

        try:
            del core
            del studio
            isy = pim.ImagingSystem(
                flow_acq_config, protocol_imaging)
            isy.execute_protocol_entry(0)
        except:
            print('skipping test as mm is not connected')
