import unittest
from unittest.mock import patch, call
import logging

import pyHamiltonPSD as ham
from PycroFlow.hamilton_architecture import LegacyArchitecture


logger = logging.getLogger(__name__)


class LegacyArchitectureTest(unittest.TestCase):
    def setUp(self):
        test_system_config = {
            'system_type': 'legacy',
            'valve_a': [
                {'address': 0, 'instrument_type': 'MVP', 'valve_type': '8-5'},
                {'address': 1, 'instrument_type': 'MVP', 'valve_type': '8-5'},
                ],
            'valve_flush': {'address': 4, 'instrument_type': 'MVP', 'valve_type': '8-5'},
            'flush_pos': {'inject': 1, 'flush': 0},
            'pump_a': {'address': 2, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '500u'},
            'pump_out': {'address': 3, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '5.0m'},
            'reservoir_a': [
                {'id': 0, 'valve_pos': {0: 3, 1: 2}},
                {'id': 1, 'valve_pos': {0: 2, 1: 2}},
                {'id': 3, 'valve_pos': {0: 2, 1: 4}},
                ],
            'special_names': {
                'flushbuffer_a': 3,  # defines the reservoir id with the buffer that can be used for flushing}
                }
            }
        test_tubing_config = {
            ('R0', 'pump_a'): 0,
            ('R1', 'pump_a'): 0,
            ('R3', 'pump_a'): 0,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 0,
        }
        test_protocol = {
            'flow_parameters': {
                'start_velocity': 50,
                'max_velocity': 1000,
                'stop_velocity': 500,
                'mode': 'tubing_stack',
                'extractionfactor': 2},
            'imaging': {
                'frames': 30000,
                't_exp': 100},
            'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 200, 'velocity': 600},
                {'$type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
                {'$type': 'inject', 'reservoir_id': 0, 'volume': 300},   # for more commplex system: 'mix'
            ]}
        patch_send_command = patch('pyHamiltonPSD.communication.sendCommand', create=True)
        patch_send_command.start()
        self.addCleanup(patch_send_command.stop)

        patch_connect = patch('pyHamiltonPSD.communication.initializeSerial', create=True)
        patch_connect.start()
        self.addCleanup(patch_connect.stop)

        patch_connect = patch('pyHamiltonPSD.initializeSerial', create=True)
        patch_connect.start()
        self.addCleanup(patch_connect.stop)

        patch_disconnect = patch('pyHamiltonPSD.communication.disconnectSerial', create=True)
        patch_disconnect.start()
        self.addCleanup(patch_disconnect.stop)

        # patch_pump = patch(__name__ + '.Pump')
        # patch_pump.start()
        # self.addCleanup(patch_pump.stop)

        # patch_valve = patch(__name__ + '.Valve')
        # patch_valve.start()
        # self.addCleanup(patch_valve.stop)

        # patch_res = patch(__name__ + '.Reservoir', autospec=True)
        # patch_res.start()
        # self.addCleanup(patch_res.stop)

        self.va = LegacyArchitecture(test_system_config, test_tubing_config)
        self.va._assign_protocol(test_protocol)

        # print(self.va.pump_a.call_args_list)
        # print(self.va.pump_out.call_args_list)

    def test_vol_to_inlet(self):
        # check vol to inlet calculation
        vol = self.va._calc_vol_to_inlet(1)
        # print(vol)
        self.assertTrue(vol == 0)
        # print(ham.communication.sendCommand.call_args_list)
        # assert False

    def test_tubing_stack_1(self):
        # check tubing column without volume in tubings
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.)],
            1: [(1, 200.)],
            2: [],
            3: [(0, 300.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_2(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            ('R0', 'pump_a'): 0,
            ('R1', 'pump_a'): 0,
            ('R3', 'pump_a'): 0,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 100,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.), (1, 100.)],
            1: [(1, 100.), (0, 100.)],
            2: [],
            3: [(0, 200.), (3, 100.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_3(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            ('R0', 'pump_a'): 100,
            ('R1', 'pump_a'): 300,
            ('R3', 'pump_a'): 200,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 0,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.), (1, 100.)],
            1: [(1, 100.), (0, 300.)],
            2: [],
            3: [(3, 200.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_set_valve(self):
        """
        test the reservoir setting
            'reservoir_a': [
                {'id': 0, 'valve_pos': {0: 3, 1: 2}},
                {'id': 1, 'valve_pos': {0: 2, 1: 2}},
                {'id': 3, 'valve_pos': {0: 2, 1: 4}},
        """
        ham.communication.sendCommand.reset_mock()
        self.va._set_valves(0)
        # logger.debug(ham.communication.sendCommand.call_args_list)
        ham.communication.sendCommand.assert_has_calls([
            call('1', 'h26003R', waitForPump=False),
            call('2', 'h26002R', waitForPump=False)])

        ham.communication.sendCommand.reset_mock()
        self.va._set_valves(3)
        ham.communication.sendCommand.assert_has_calls([
            call('1', 'h26002R', waitForPump=False),
            call('2', 'h26004R', waitForPump=False)])

    def test_inject(self):
        """Test system injection
        """
        ham.communication.sendCommand.reset_mock()
        try:
            self.va._inject(10)
        except ValueError as e:
            # hamilton devices are not connected. skip
            print('skipping test as hamilton is not connected')
            return

        # logger.debug(ham.communication.sendCommand.call_args_list)
        ham.communication.sendCommand.assert_has_calls([
            call('5', 'h26001R', waitForPump=False),
            call('3', 'IR', waitForPump=True),
            call('4', 'OR', waitForPump=True),
            call('3', 'V1000P480R', waitForPump=False),
            call('4', 'V200D0R', waitForPump=False),
            call('3', 'Q', waitForPump=True),
            call('4', 'Q', waitForPump=True),
            call('3', 'OR', waitForPump=True),
            call('4', 'IR', waitForPump=True),
            call('3', 'V1000D480R', waitForPump=False),
            call('4', 'V200P96R', waitForPump=False),
            call('3', 'Q', waitForPump=True),
            call('4', 'Q', waitForPump=True),
            call('4', 'OR', waitForPump=True),
            call('4', 'V200D96R', waitForPump=False),
            call('4', 'Q', waitForPump=True)])

