import unittest
from unittest.mock import MagicMock
import logging
import threading
import time

import PycroFlow.orchestration as por


logger = logging.getLogger(__name__)


class TestOrchestration(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01(self):
        threadexchange = {
            'fluid_lock': threading.Lock(),
            'fluid': [],
            'imaging_lock': threading.Lock(),
            'imaging': [],
            'illumination_lock': threading.Lock(),
            'illumination': [],
            'pause_flag': threading.Event(),
            'abort_flag': threading.Event()
        }
        protocol_fluid = [
            {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
            {'$type': 'incubate', 'duration': 120},
            {'$type': 'inject', 'reservoir_id': 1, 'volume': 500,
             'velocity': 600},
            {'$type': 'signal', 'value': 'fluid round 1 done'},
            {'$type': 'flush', 'flushfactor': 1},
            {'$type': 'wait for signal', 'target': 'imaging',
             'value': 'round 1 done'},
            {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
        ]
        fh = por.FluidHandler(MagicMock(), protocol_fluid, threadexchange)
        fh.execute_protocol_entry(0)

        threadexchange['abort_flag'].set()
        fh.main_loop()

    def test_02(self):
        threadexchange = {
            'fluid_lock': threading.Lock(),
            'fluid': [],
            'imaging_lock': threading.Lock(),
            'imaging': [],
            'illumination_lock': threading.Lock(),
            'illumination': [],
            'pause_flag': threading.Event(),
            'abort_flag': threading.Event()
        }
        protocol_fluid = [
            {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
            {'$type': 'incubate', 'duration': 120},
            {'$type': 'inject', 'reservoir_id': 1, 'volume': 500,
             'velocity': 600},
            {'$type': 'signal', 'value': 'fluid round 1 done'},
            {'$type': 'flush', 'flushfactor': 1},
            {'$type': 'wait for signal', 'target': 'imaging',
             'value': 'round 1 done'},
            {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
        ]
        fh = por.FluidHandler(MagicMock(), protocol_fluid, threadexchange)
        fh.start()
        # now running in separate thread
        time.sleep(.1)
        threadexchange['abort_flag'].set()