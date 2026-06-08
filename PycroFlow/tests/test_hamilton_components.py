"""Tests for hamilton_components: tubing/reservoir data structures (pure
logic) and Pump/Valve driven against the Hamilton serial emulator."""
import threading
import unittest

import PycroFlow.pyHamilton as ham
from PycroFlow.hamilton_components import (
    Reservoir, ReservoirDict, TubingConfig, Pump, Valve,
)
from PycroFlow.tests.emulators import patch_serial


# ---------------------------------------------------------------------------
# Pure data structures (no hardware)
# ---------------------------------------------------------------------------

class TubingConfigTest(unittest.TestCase):
    def test_get_returns_entry(self):
        tc = TubingConfig({('R0', 'pump_a'): 42})
        self.assertEqual(tc.get('R0', 'pump_a'), 42)

    def test_reservoir_to_pump_direct(self):
        tc = TubingConfig({('R2', 'pump_a'): 17})
        self.assertEqual(tc.get_reservoir_to_pump(2, 'a'), 17)

    def test_reservoir_to_pump_assembled_along_segments(self):
        tc = TubingConfig({('R2', 'V0'): 10, ('V0', 'pump_a'): 5})
        self.assertEqual(tc.get_reservoir_to_pump(2, 'a'), 15)

    def test_reservoir_to_pump_via_special_name(self):
        tc = TubingConfig({('R2', 'pump_a'): 8})
        tc.set_special_names({'buffer': 2})
        self.assertEqual(tc.get_reservoir_to_pump('buffer', 'a'), 8)

    def test_reservoir_to_closest_valve(self):
        tc = TubingConfig({('R2', 'V0'): 11})
        self.assertEqual(tc.get_reservoir_to_closest_valve(2), 11)

    def test_reservoir_to_closest_valve_missing_raises(self):
        tc = TubingConfig({('R2', 'sample'): 11})
        with self.assertRaises(KeyError):
            tc.get_reservoir_to_closest_valve(2)

    def test_set_reservoir_to_pump(self):
        tc = TubingConfig({})
        tc.set_reservoir_to_pump(3, 'a', 99)
        self.assertEqual(tc.get_reservoir_to_pump(3, 'a'), 99)


class ReservoirDictTest(unittest.TestCase):
    def test_add_and_lookup(self):
        rd = ReservoirDict()
        rd.add(Reservoir(0, {0: 3, 1: 2}))
        rd.add(Reservoir(1, {0: 2}))
        self.assertEqual(rd.len, 2)
        self.assertEqual(rd.get_reservoir_nvalves(0), 2)
        self.assertEqual(rd.get_reservoir_nvalves(1), 1)
        self.assertEqual(rd.get_reservoir_valve_positions(0), {0: 3, 1: 2})

    def test_accepts_str_ids(self):
        rd = ReservoirDict()
        rd.add(Reservoir(0, {0: 3}))
        # input functions may pass string ids
        self.assertEqual(rd.get_reservoir_nvalves('0'), 1)

    def test_reservoir_nvalves_property(self):
        self.assertEqual(Reservoir(0, {0: 1, 1: 2, 2: 3}).nvalves, 3)


# ---------------------------------------------------------------------------
# Pump / Valve against the serial emulator
# ---------------------------------------------------------------------------

class HamiltonDeviceTest(unittest.TestCase):
    def setUp(self):
        self.flag = threading.Event()
        self._saved = ham.communication.abort_wait_response_flag
        ham.communication.abort_wait_response_flag = threading.Event()
        self._ctx = patch_serial()
        self.fake = self._ctx.__enter__()
        ham.connect('18', 9600)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        ham.communication.abort_wait_response_flag = self._saved

    def _pump(self, output_pos='out'):
        return Pump('2', '500u', instrument_type='4', valve_type='Y',
                    output_pos=output_pos, input_pos='in', waste_pos=1,
                    pause_flag=self.flag, abort_flag=self.flag)

    def test_construct_with_input_output_position(self):
        # output_pos == 'in' exercises the 'Y' init branch.
        pump = self._pump(output_pos='in')
        self.assertEqual(pump.syringe_volume, 500.0)

    def test_get_status_returns_response(self):
        pump = self._pump()
        self.assertIn('`', pump.get_status())

    def test_stop_current_move_is_safe(self):
        pump = self._pump()
        pump.stop_current_move()  # sends terminate; emulator acks

    def test_decode_response_valid(self):
        pump = self._pump()
        self.assertEqual(pump.decode_response('/0`12000\x03'), '12000')

    def test_decode_response_not_ready_raises(self):
        pump = self._pump()
        with self.assertRaises(ValueError):
            pump.decode_response('/0@\x03')   # busy, no ready byte

    def test_decode_response_incomplete_raises(self):
        pump = self._pump()
        with self.assertRaises(ValueError):
            pump.decode_response('/0`12000')  # no ETX

    def test_set_velocity_sends_command(self):
        # Regression: set_velocity used to raise TypeError from a stray unary
        # '+' on the first command string. It must now build and send the
        # start/max/stop velocity command. Values chosen to convert in-range.
        pump = self._pump()
        before = len(self.fake.command_log)
        pump.set_velocity(100, 1000, 100)  # µL/min
        sent = self.fake.command_log[before:]
        self.assertTrue(any('V' in msg for _, msg in sent),
                        "set_velocity issued no max-velocity command")

    def test_velocity_conversion_round_trips(self):
        pump = self._pump()
        sps = pump.velocity_upm2sps(600)
        self.assertIsInstance(sps, int)
        self.assertAlmostEqual(pump.velocity_sps2upm(sps), 600, delta=5)

    def test_set_valve_none_is_noop(self):
        pump = self._pump()
        # output_pos resolves; passing None directly returns early.
        pump.set_valve(None)

    def test_resume_current_move_after_pickup(self):
        pump = self._pump()
        pump.pickup(100, waitForPump=True)
        pump.wait_until_done()
        # resume recomputes missing volume from the emulated syringe position.
        pump.resume_current_move()

    def test_valve_deferred_move_returns_exec_command(self):
        valve = Valve('1', 'MVP', '8-5')
        valve.pause_flag = self.flag
        valve.abort_flag = self.flag
        exec_cmd = valve.set_valve(3, move_now=False)
        # move_now=False returns the execute-buffer command for later.
        self.assertEqual(exec_cmd, 'R')
        valve.wait_until_done()
        self.assertIn('`', valve.get_status())


if __name__ == '__main__':
    unittest.main()
