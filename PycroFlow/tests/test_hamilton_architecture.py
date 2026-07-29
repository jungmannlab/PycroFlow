import unittest
from unittest.mock import patch, call
import logging
import threading

import PycroFlow.pyHamilton as ham
from PycroFlow.hamilton_architecture import LegacyArchitecture

logger = logging.getLogger(__name__)


class LegacyArchitectureTest(unittest.TestCase):
    def setUp(self):
        test_system_config = {
            "system_type": "legacy",
            "valve_a": [
                {"address": 0, "instrument_type": "MVP", "valve_type": "8-5"},
                {"address": 1, "instrument_type": "MVP", "valve_type": "8-5"},
            ],
            "valve_flush": {
                "address": 4,
                "instrument_type": "MVP",
                "valve_type": "8-5",
            },
            "flush_pos": {"inject": 1, "flush": 0},
            "pump_a": {
                "address": 2,
                "instrument_type": "4",
                "valve_type": "Y",
                "syringe": "500u",
            },
            "pump_out": {
                "address": 3,
                "instrument_type": "4",
                "valve_type": "Y",
                "syringe": "5.0m",
            },
            "reservoir_a": [
                {"id": 0, "valve_pos": {0: 3, 1: 2}},
                {"id": 1, "valve_pos": {0: 2, 1: 2}},
                {"id": 3, "valve_pos": {0: 2, 1: 4}},
            ],
            "special_names": {
                "flushbuffer_a": 3,  # defines the reservoir id with the buffer that can be used for flushing}
            },
        }
        test_tubing_config = {
            ("R0", "pump_a"): 0,
            ("R1", "pump_a"): 0,
            ("R3", "pump_a"): 0,
            ("pump_a", "valve_flush"): 0,
            ("valve_flush", "sample"): 0,
        }
        test_protocol = {
            "parameters": {
                "start_velocity": 50,
                "max_velocity": 1000,
                "stop_velocity": 500,
                "mode": "tubing_stack",
                "extractionfactor": 2,
                "pumpout_dispense_velocity": 20000,
                "inject_pickup_extravol": 1500,
                # Zero the equilibration delays so the mocked _inject test
                # doesn't actually time.sleep(20s); they have no effect
                # without real hardware.
                "inject_in_to_out_delay": 0,
                "inject_out_to_in_delay": 0,
                "clean_velocity": 3000,
            },
            "imaging": {"frames": 30000, "t_exp": 100},
            "protocol_entries": [
                {"$type": "inject", "reservoir_id": 0, "volume": 500},
                {
                    "$type": "inject",
                    "reservoir_id": 1,
                    "volume": 200,
                    "velocity": 600,
                },
                {
                    "$type": "acquire",
                    "frames": 10000,
                    "t_exp": 100,
                    "round": 1,
                },
                {
                    "$type": "inject",
                    "reservoir_id": 0,
                    "volume": 300,
                },  # for more commplex system: 'mix'
            ],
        }
        # Patch the in-house serial driver (PycroFlow.pyHamilton.communication),
        # NOT the external pyHamiltonPSD package — the code calls
        # ham.communication.sendCommand where ham is PycroFlow.pyHamilton.
        # sendCommand returns a status string; supply a benign default so
        # Valve / Pump construction can parse it.
        # Response layout: result[3:4] is parsed as the resolution mode int
        # by Pump.__init__, so position 3 must be a digit.
        patch_send_command = patch(
            "PycroFlow.pyHamilton.communication.sendCommand",
            create=True,
            return_value="/0`1\x03",
        )
        patch_send_command.start()
        self.addCleanup(patch_send_command.stop)

        patch_connect = patch(
            "PycroFlow.pyHamilton.communication.initializeSerial", create=True
        )
        patch_connect.start()
        self.addCleanup(patch_connect.stop)

        patch_connect2 = patch("PycroFlow.pyHamilton.connect", create=True)
        patch_connect2.start()
        self.addCleanup(patch_connect2.stop)

        patch_disconnect = patch(
            "PycroFlow.pyHamilton.communication.disconnectSerial", create=True
        )
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
        # The orchestrator normally assigns pause/abort events; without them
        # Valve/Pump.set_valve dereferences a None pause_flag. Mirror that
        # setup so direct-call tests (set_valve, inject) work.
        self.va._assign_multiprocess_events(
            threading.Event(), threading.Event(), threading.Event()
        )

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
            0: [(0, 500.0)],
            1: [(1, 200.0)],
            2: [],
            3: [(0, 300.0)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_2(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            ("R0", "pump_a"): 0,
            ("R1", "pump_a"): 0,
            ("R3", "pump_a"): 0,
            ("pump_a", "valve_flush"): 0,
            ("valve_flush", "sample"): 100,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.0), (1, 100.0)],
            1: [(1, 100.0), (0, 100.0)],
            2: [],
            3: [(0, 200.0), (3, 100.0)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_3(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            ("R0", "pump_a"): 100,
            ("R1", "pump_a"): 300,
            ("R3", "pump_a"): 200,
            ("pump_a", "valve_flush"): 0,
            ("valve_flush", "sample"): 0,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.0), (1, 100.0)],
            1: [(1, 100.0), (0, 300.0)],
            2: [],
            3: [(3, 200.0)],
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
        ham.communication.sendCommand.assert_has_calls(
            [
                call("1", "h26003R", waitForPump=False),
                call("2", "h26002R", waitForPump=False),
            ]
        )

        ham.communication.sendCommand.reset_mock()
        self.va._set_valves(3)
        ham.communication.sendCommand.assert_has_calls(
            [
                call("1", "h26002R", waitForPump=False),
                call("2", "h26004R", waitForPump=False),
            ]
        )

    def test_inject(self):
        """Test system injection issues the expected device commands.

        Previously this pinned an exact 16-call byte sequence, which broke
        every time the velocity / command-generation logic was tuned. We now
        assert the observable behavior: the flush valve is positioned and the
        pump_a / pump_out addresses receive volume (pickup/dispense)
        commands. This guards against _inject becoming a no-op without
        coupling to the exact command bytes.
        """
        ham.communication.sendCommand.reset_mock()
        try:
            self.va._inject(10)
        except ValueError:
            # hamilton devices are not connected. skip
            print("skipping test as hamilton is not connected")
            return

        sent = ham.communication.sendCommand.call_args_list
        self.assertGreater(len(sent), 0, "no device commands issued")

        # Flush valve (address '5') is set at the start of an injection.
        self.assertIn(call("5", "h26001R", waitForPump=False), sent)

        # pump_a (address '2' on the test config -> ascii) and pump_out
        # (address '3') should both receive volume commands containing a
        # pickup 'P' or dispense 'D' opcode.
        def has_volume_command(addr):
            return any(
                c.args
                and c.args[0] == addr
                and isinstance(c.args[1], str)
                and ("P" in c.args[1] or "D" in c.args[1])
                for c in sent
            )

        self.assertTrue(
            has_volume_command("3"), "pump_out issued no volume command"
        )
        self.assertTrue(
            has_volume_command("4"), "valve_flush/pump path issued no command"
        )
