import unittest
from unittest.mock import MagicMock, call
import logging
import time

import PycroFlow.orchestration as por
from PycroFlow.orchestration import ThreadExchange


logger = logging.getLogger(__name__)


def _wrap(entries):
    """Handlers expect a {'protocol_entries': [...]} dict, not a bare list."""
    return {'protocol_entries': entries}


class TestOrchestration(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def get_threadexchange(self):
        # Use the production ThreadExchange so the test exercises the same
        # keys ('img'/'illu', not 'imaging'/'illumination') and ships a
        # SignalRegistry. Previously this test hand-rolled a dict with the
        # wrong subsystem key names.
        return ThreadExchange.create()

    def test_01(self):
        threadexchange = self.get_threadexchange()
        protocol_fluid = [
            {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
            {'$type': 'incubate', 'duration': 120},
            {'$type': 'inject', 'reservoir_id': 1, 'volume': 500,
             'velocity': 600},
            {'$type': 'signal', 'value': 'fluid round 1 done'},
            {'$type': 'flush', 'flushfactor': 1},
            {'$type': 'wait for signal', 'target': 'img',
             'value': 'round 1 done'},
            {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
        ]
        fh = por.FluidHandler(MagicMock(), _wrap(protocol_fluid), threadexchange)
        fh.execute_protocol_entry(0)

        threadexchange['abort_flag'].set()
        fh.run()

    def test_02(self):
        logger.debug('TESTING FluidHandler')
        threadexchange = self.get_threadexchange()
        protocol_fluid = [
            {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
            {'$type': 'signal', 'value': 'fluid round 1 done'},
            {'$type': 'wait for signal', 'target': 'img',
             'value': 'round 1 done'},
            {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
        ]
        dummy_system = MagicMock()
        fh = por.FluidHandler(dummy_system, _wrap(protocol_fluid), threadexchange)
        threadexchange['start_protocol_flag'].set()
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()
        threadexchange['abort_protocol_flag'].set()
        fh.join(timeout=2)

        # The handler emits an 'Ending.' marker via send_message when it
        # aborts during housekeeping, so the message log may carry it after
        # the protocol signal.
        self.assertIn('fluid round 1 done', threadexchange['fluid'])
        # The handler also calls system setup (_assign_protocol,
        # _assign_multiprocess_events) and abort_execution on shutdown, so
        # check the inject step was executed rather than asserting the exact
        # call list.
        self.assertIn(call.execute_protocol_entry(0), dummy_system.method_calls)

    def test_03(self):
        logger.debug('TESTING ImagingHandler')
        threadexchange = self.get_threadexchange()
        protocol_img = [
            {'$type': 'acquire', 'frames': 1000, 't_exp': 100},
            {'$type': 'signal', 'value': 'imaging round 1 done'},
            {'$type': 'wait for signal', 'target': 'img',
             'value': 'round 1 done'},
        ]
        dummy_system = MagicMock()
        fh = por.ImagingHandler(dummy_system, _wrap(protocol_img), threadexchange)
        threadexchange['start_protocol_flag'].set()
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()
        fh.join(timeout=2)

        self.assertIn('imaging round 1 done', threadexchange['img'])
        self.assertIn(call.execute_protocol_entry(0), dummy_system.method_calls)

    def test_04(self):
        logger.debug('TESTING IlluminationHandler')
        threadexchange = self.get_threadexchange()
        protocol_illu = [
            {'$type': 'power', 'value': 20},
            {'$type': 'signal', 'value': 'illumination round 1 done'},
            {'$type': 'wait for signal', 'target': 'img',
             'value': 'round 1 done'},
        ]
        dummy_system = MagicMock()
        fh = por.IlluminationHandler(dummy_system, _wrap(protocol_illu), threadexchange)
        threadexchange['start_protocol_flag'].set()
        fh.start()
        # now running in separate thread
        time.sleep(1)
        threadexchange['abort_flag'].set()
        fh.join(timeout=2)

        self.assertIn('illumination round 1 done', threadexchange['illu'])
        self.assertIn(call.execute_protocol_entry(0), dummy_system.method_calls)

    def test_05(self):
        logger.debug('TESTING Orchestration')

        protocol = {
            'fluid': _wrap([
                {'$type': 'signal', 'value': 'fluid round 1 done'},
                {'$type': 'wait for signal', 'target': 'img',
                 'value': 'imaging round 1 done'}]),
            'img': _wrap([
                {'$type': 'wait for signal', 'target': 'fluid',
                 'value': 'fluid round 1 done'},
                {'$type': 'signal', 'value': 'imaging round 1 done'}]),
        }
        dummy_fluid = MagicMock()
        dummy_imaging = MagicMock()
        po = por.ProtocolOrchestrator(
            protocol, fluid_system=dummy_fluid, imaging_system=dummy_imaging)
        po.start_orchestration()
        po.start_protocol()
        # now running in separate thread
        time.sleep(1)
        logger.debug('protocol finished' + str(po.poll_protocol_finished()))
        po.end_orchestration()

        self.assertEqual(po.threadexchange['fluid'], ['fluid round 1 done'])
        self.assertEqual(po.threadexchange['img'], ['imaging round 1 done'])

    def test_illumination_handler_assigns_protocol(self):
        # Regression: IlluminationHandler must assign the protocol to its
        # system (like Fluid/Imaging) or execute_protocol_entry raises
        # AttributeError('IlluminationSystem' has no attribute 'protocol').
        from PycroFlow.tests.emulators import EmulatedIlluminationSystem
        protocol = {'illu': _wrap([
            {'$type': 'set power', 'laser': 1, 'power': 5}])}
        illu = EmulatedIlluminationSystem()
        por.ProtocolOrchestrator(protocol, illumination_system=illu)
        self.assertIsNotNone(illu.protocol)
        self.assertEqual(
            illu.protocol['protocol_entries'][0]['$type'], 'set power')


if __name__ == '__main__':
    unittest.main()
