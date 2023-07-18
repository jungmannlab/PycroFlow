import unittest
from unittest.mock import MagicMock, call
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
        fh.run()

    def test_02(self):
        logger.debug('TESTING FluidHandler')
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
            {'$type': 'signal', 'value': 'fluid round 1 done'},
            {'$type': 'wait for signal', 'target': 'imaging',
             'value': 'round 1 done'},
            {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
        ]
        dummy_system = MagicMock()
        fh = por.FluidHandler(dummy_system, protocol_fluid, threadexchange)
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()

        txch_expected = ['fluid round 1 done']
        self.assertEqual(threadexchange['fluid'], txch_expected)
        calls_expect = [call.execute_protocol_entry(0)]
        self.assertEqual(dummy_system.method_calls, calls_expect)

    def test_03(self):
        logger.debug('TESTING ImagingHandler')
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
            {'$type': 'acquire', 'frames': 1000, 't_exp': 100},
            {'$type': 'signal', 'value': 'imaging round 1 done'},
            {'$type': 'wait for signal', 'target': 'imaging',
             'value': 'round 1 done'},
        ]
        dummy_system = MagicMock()
        fh = por.ImagingHandler(dummy_system, protocol_fluid, threadexchange)
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()

        txch_expected = ['imaging round 1 done']
        self.assertEqual(threadexchange['imaging'], txch_expected)
        calls_expect = [call.execute_protocol_entry(0)]
        self.assertEqual(dummy_system.method_calls, calls_expect)

    def test_04(self):
        logger.debug('TESTING IlluminationHandler')
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
            {'$type': 'power', 'value': 20},
            {'$type': 'signal', 'value': 'illumination round 1 done'},
            {'$type': 'wait for signal', 'target': 'imaging',
             'value': 'round 1 done'},
        ]
        dummy_system = MagicMock()
        fh = por.IlluminationHandler(dummy_system, protocol_fluid, threadexchange)
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()

        txch_expected = ['illumination round 1 done']
        self.assertEqual(threadexchange['illumination'], txch_expected)
        calls_expect = [call.execute_protocol_entry(0)]
        self.assertEqual(dummy_system.method_calls, calls_expect)

    def test_05(self):
        logger.debug('TESTING Orchestration')

        protocol = {
            'fluid': [
                {'$type': 'signal', 'value': 'fluid round 1 done'},
                {'$type': 'wait for signal', 'target': 'imaging',
                 'value': 'imaging round 1 done'}],
            'imaging': [
                {'$type': 'wait for signal', 'target': 'fluid',
                 'value': 'fluid round 1 done'},
                {'$type': 'signal', 'value': 'imaging round 1 done'}],
        }
        dummy_fluid = MagicMock()
        dummy_imaging = MagicMock()
        po = por.ProtocolOrchestrator(
            protocol, fluid_system=dummy_fluid, imaging_system=dummy_imaging)
        po.start_orchestration()
        # now running in separate thread
        time.sleep(1)
        # po.abort_orchestration()
        po.poll_orchestration_finished()
        po.end_orchestration()

        txch_expected = ['fluid round 1 done']
        self.assertEqual(po.threadexchange['fluid'], txch_expected)
        txch_expected = ['imaging round 1 done']
        self.assertEqual(po.threadexchange['imaging'], txch_expected)
        calls_expect = []
        self.assertEqual(dummy_fluid.method_calls, calls_expect)
