"""High-level coverage for LegacyArchitecture, driven against the Hamilton
serial emulator so the real Pump/Valve command paths execute.

The byte-level inject/valve/tubing-stack behavior is pinned in
test_hamilton_architecture; here we exercise the orchestration-facing methods
(protocol dispatch, deliver_fluid, fill_tubings, flush, pump_out, and the
pause/resume/abort/stop lifecycle) that those tests don't reach.
"""
import threading
import unittest

import PycroFlow.pyHamilton as ham
from PycroFlow.hamilton_components import ReservoirDict
from PycroFlow import fluid as _fluid_pkg
from PycroFlow.fluid import legacy as fluid_legacy
from PycroFlow.fluid.legacy import LegacyArchitecture
from PycroFlow.tests.emulators import patch_serial


SYSTEM_CONFIG = {
    'system_type': 'legacy',
    'valve_a': [
        {'address': 0, 'instrument_type': 'MVP', 'valve_type': '8-5'},
        {'address': 1, 'instrument_type': 'MVP', 'valve_type': '8-5'},
    ],
    'valve_flush': {'address': 4, 'instrument_type': 'MVP', 'valve_type': '8-5'},
    # flush position must be a valid MVP position (1-8); 'flush' is used by
    # fill_tubings/_flush.
    'flush_pos': {'inject': 1, 'flush': 2},
    'pump_a': {'address': 2, 'instrument_type': '4', 'valve_type': 'Y',
               'syringe': '500u'},
    'pump_out': {'address': 3, 'instrument_type': '4', 'valve_type': 'Y',
                 'syringe': '5.0m'},
    'reservoir_a': [
        {'id': 0, 'valve_pos': {0: 3, 1: 2}},
        {'id': 1, 'valve_pos': {0: 2, 1: 2}},
        {'id': 3, 'valve_pos': {0: 2, 1: 4}},
    ],
    'special_names': {'flushbuffer_a': 3},
}

TUBING_CONFIG = {
    ('R0', 'pump_a'): 0,
    ('R1', 'pump_a'): 0,
    ('R3', 'pump_a'): 0,
    ('pump_a', 'valve_flush'): 50,   # nonzero so _flush actually pumps
    ('valve_flush', 'sample'): 0,
}

PARAMETERS = {
    'start_velocity': 50,
    'max_velocity': 1000,
    'stop_velocity': 500,
    'mode': 'tubing_ignore',
    'extractionfactor': 2,
    'pumpout_dispense_velocity': 20000,
    'inject_pickup_extravol': 1500,
    # Zero delays exercise the empty-pump path of _inject that used to raise
    # ZeroDivisionError (regression guard for that fix).
    'inject_in_to_out_delay': 0,
    'inject_out_to_in_delay': 0,
    'clean_velocity': 3000,
}

INJECT_PROTOCOL = {
    'parameters': dict(PARAMETERS),
    'protocol_entries': [
        {'$type': 'inject', 'reservoir_id': 0, 'volume': 10},
        {'$type': 'inject', 'reservoir_id': 1, 'volume': 10},
    ],
}


class LegacyArchitectureTest(unittest.TestCase):
    def setUp(self):
        # LegacyArchitecture keeps device collections as *class* attributes;
        # reset them so instances across tests don't alias each other.
        LegacyArchitecture.valve_a = {}
        LegacyArchitecture.valve_flush = None
        LegacyArchitecture.reservoir_a = ReservoirDict()
        LegacyArchitecture.reservoir_paths = {}
        LegacyArchitecture.last_protocol_entry = -1
        fluid_legacy.is_connected = False

        self._saved_abort = ham.communication.abort_wait_response_flag
        ham.communication.abort_wait_response_flag = threading.Event()

        self._ctx = patch_serial()
        self.fake = self._ctx.__enter__()
        # LegacyArchitecture builds (and immediately addresses) its devices in
        # __init__ before its own connect() call, so the bus must already hold
        # the emulated serial. Connect first, then mark connected so __init__
        # skips reconnecting.
        ham.connect('18', 9600)
        fluid_legacy.is_connected = True

        self.la = LegacyArchitecture(SYSTEM_CONFIG, TUBING_CONFIG)
        self.la._assign_protocol({'parameters': dict(PARAMETERS),
                                  'protocol_entries': list(
                                      INJECT_PROTOCOL['protocol_entries'])})
        self.la._assign_multiprocess_events(
            threading.Event(), threading.Event(), threading.Event())

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        ham.communication.abort_wait_response_flag = self._saved_abort

    # --- construction / connectivity -------------------------------------

    def test_construction_builds_devices(self):
        self.assertIsNotNone(self.la.pump_a)
        self.assertIsNotNone(self.la.pump_out)
        # two MVP valves + pump_a registered into valve_a
        self.assertIn(0, self.la.valve_a)
        self.assertIn(1, self.la.valve_a)

    def test_test_communication_runs(self):
        self.la._test_communication()  # must not raise against the emulator

    # --- protocol dispatch -----------------------------------------------

    def test_execute_protocol_entry_tubing_ignore_inject(self):
        self.la.parameters['mode'] = 'tubing_ignore'
        before = len(self.fake.command_log)
        self.la.execute_protocol_entry(0)
        self.assertGreater(len(self.fake.command_log), before)

    def test_execute_protocol_entry_tubing_stack_inject(self):
        self.la.parameters['mode'] = 'tubing_stack'
        before = len(self.fake.command_log)
        self.la.execute_protocol_entry(0)
        self.assertGreater(len(self.fake.command_log), before)
        # tubing_stack records the last executed entry
        self.assertEqual(self.la.last_protocol_entry, 0)

    def test_execute_single_protocol_entry_pump_out(self):
        self.la.protocol = [{'$type': 'pump_out', 'volume': 50}]
        self.la.parameters['mode'] = 'tubing_ignore'
        before = len(self.fake.command_log)
        self.la.execute_protocol_entry(0)
        self.assertGreater(len(self.fake.command_log), before)
        # single-entry execution invalidates the tubing stack
        self.assertEqual(self.la.last_protocol_entry, -1)

    def test_unknown_mode_raises(self):
        self.la.parameters['mode'] = 'nonsense'
        with self.assertRaises(Exception):
            self.la.execute_protocol_entry(0)

    # --- direct fluid operations -----------------------------------------

    def test_deliver_fluid(self):
        before = len(self.fake.command_log)
        self.la.deliver_fluid(0, 20)
        self.assertGreater(len(self.fake.command_log), before)

    def test_pump_out_method(self):
        before = len(self.fake.command_log)
        self.la._pump_out(50)
        self.assertGreater(len(self.fake.command_log), before)
        # pump_out picked up then dispensed -> ascii address '4'
        self.assertTrue(any(a == '4' for a, _ in self.fake.command_log))

    def test_fill_tubings_returns_total_volume(self):
        total = self.la.fill_tubings(extra_vol=10)
        self.assertGreater(total, 0)

    def test_flush(self):
        before = len(self.fake.command_log)
        self.la._flush(flushfactor=1)
        self.assertGreater(len(self.fake.command_log), before)

    # --- lifecycle -------------------------------------------------------

    def test_pause_sets_flags_and_stops(self):
        self.la.pause_execution(msg="unit test")
        self.assertTrue(self.la.pause_flag.is_set())
        self.assertTrue(ham.communication.abort_wait_response_flag.is_set())

    def test_abort_sets_flag(self):
        self.la.abort_execution()
        self.assertTrue(self.la.abort_flag.is_set())

    def test_stop_all_moves_is_safe(self):
        self.la.stop_all_moves()

    def test_resume_returns_true_when_not_aborted(self):
        # No pause/abort in flight -> resume completes and reports success.
        ham.communication.abort_wait_response_flag.clear()
        self.assertTrue(self.la.resume_execution())


if __name__ == '__main__':
    unittest.main()
