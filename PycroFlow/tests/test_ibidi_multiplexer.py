"""Tests for the ibidi MultiFlOW 24-channel multiplexer driver.

The real :class:`PycroFlow.ibidi_multiplexer.IbidiMultiplexer` runs against
the serial-level :class:`FakeIbidiSerial` so the command encode / response
decode path gets genuine coverage, plus an integration test that builds a
``LegacyArchitecture`` from the ``IbidiEmulator`` setup and routes reservoirs.
"""
import threading
import unittest

import PycroFlow.pyHamilton as ham
from PycroFlow.hal import Valve as ValveABC
from PycroFlow.ibidi_multiplexer import IbidiMultiplexer
from PycroFlow.tests import emulators as emu


class IbidiMultiplexerDriverTest(unittest.TestCase):
    """Drive the real driver against the fake serial firmware."""

    def test_identification_queries(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer('7')
            self.assertIn('MX', mx.identify())
            self.assertEqual(mx.firmware_version().strip(';'), '0.0.2')
            self.assertIn('Matrix_Aktorblock', mx.hardware_version())
            self.assertEqual(mx.num_channels(), 24)
            self.assertTrue(mx.get_status())
            mx.close()

    def test_is_a_hal_valve(self):
        self.assertTrue(issubclass(IbidiMultiplexer, ValveABC))

    def test_select_is_exclusive_and_atomic(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.select(3)
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [3])
            mx.select(20)
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [20])
            # exactly one SETBATCHVALVES per select (atomic), no UNSETALL churn
            batch = [c for c, _ in fake.command_log
                     if c.startswith('SETBATCHVALVES')]
            self.assertEqual(len(batch), 2)
            mx.close()

    def test_select_multiple_channels_exclusively(self):
        # Unlike a rotary valve, several channels may be open at once — but
        # everything not listed must still be closed, in one atomic command.
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.select([1, 3])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [1, 3])
            # switching to another set closes the previous one
            mx.select([2, 24])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [2, 24])
            batch = [c for c, _ in fake.command_log
                     if c.startswith('SETBATCHVALVES')]
            self.assertEqual(len(batch), 2)
            mx.close()

    def test_set_valve_accepts_channel_list(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.set_valve([4, 5])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [4, 5])
            mx.close()

    def test_select_rejects_bad_channel_sets(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer('7')
            with self.assertRaises(ValueError):
                mx.select([])            # no channel at all
            with self.assertRaises(ValueError):
                mx.select([1, 25])       # out of range
            mx.close()

    def test_set_valve_matches_select(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.set_valve(7)
            self.assertTrue(fake.channels[6])
            self.assertEqual(sum(fake.channels), 1)
            mx.close()

    def test_set_all_unset_all(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.set_all()
            self.assertTrue(all(fake.channels))
            mx.unset_all()
            self.assertFalse(any(fake.channels))
            mx.close()

    def test_set_single_channel(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.set_channel(5, True)
            self.assertTrue(fake.channels[4])
            mx.set_channel(5, False)
            self.assertFalse(fake.channels[4])
            mx.close()

    def test_channel_out_of_range_raises(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer('7')
            with self.assertRaises(ValueError):
                mx.select(0)
            with self.assertRaises(ValueError):
                mx.select(25)
            mx.close()

    def test_batch_wrong_length_raises(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer('7')
            with self.assertRaises(ValueError):
                mx.set_batch([1, 0, 1])
            mx.close()

    def test_clear_faults_and_status_registers(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer('7')
            self.assertIn('OK', mx.clear_faults())
            self.assertIn('DRV0', mx.read_status(0))
            mx.close()

    def test_abort_flag_skips_switch(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer('7')
            mx.abort_flag.set()
            mx.set_valve(4)
            # no channel opened while aborted
            self.assertFalse(any(fake.channels))
            mx.close()

    def test_connect_false_does_no_io(self):
        # No patch needed: constructor must not touch serial.
        mx = IbidiMultiplexer('7', connect=False)
        self.assertIsNone(mx._serial)
        with self.assertRaises(RuntimeError):
            mx.identify()


class IbidiLegacyArchitectureIntegrationTest(unittest.TestCase):
    """Build the real LegacyArchitecture from the IbidiEmulator setup."""

    def setUp(self):
        # valve_a / reservoir_a are class-level in LegacyArchitecture and
        # accumulate across instances; reset so a prior test's Hamilton valves
        # don't linger in _test_communication.
        import PycroFlow.hamilton_architecture as ha
        from PycroFlow.hamilton_components import ReservoirDict
        ha.LegacyArchitecture.valve_a = {}
        ha.LegacyArchitecture.reservoir_a = ReservoirDict()
        self._saved_abort = ham.communication.abort_wait_response_flag
        ham.communication.abort_wait_response_flag = threading.Event()

    def tearDown(self):
        ham.communication.abort_wait_response_flag = self._saved_abort

    def _build(self):
        from PycroFlow.configs import load_setup, assemble_hamilton_config
        import PycroFlow.hamilton_architecture as ha

        setup = load_setup('IbidiEmulator')
        settings = {
            'reservoir_names': {1: 'imager1', 2: 'imager2', 5: 'buffer'},
            'special_names': {'flushbuffer_a': 5},
        }
        hamilton, tubing = assemble_hamilton_config(setup, settings)
        with emu.patch_serial(), emu.patch_ibidi_serial() as fake:
            ha.connect(hamilton['interface']['COM'],
                       hamilton['interface']['baud'])
            la = ha.LegacyArchitecture(hamilton, tubing)
        return la, fake

    def test_builds_with_multiplexer(self):
        la, fake = self._build()
        self.assertIsNotNone(la.multiplexer)
        self.assertIsInstance(la.multiplexer, IbidiMultiplexer)
        self.assertIn('ibidi', la.valve_a)

    def test_set_valves_routes_multi_channel_reservoir(self):
        # A reservoir wired through several channels (valve_pos {ibidi: [..]})
        # opens exactly those, all the way from _set_valves.
        from PycroFlow.configs import assemble_hamilton_config, load_setup
        import PycroFlow.hamilton_architecture as ha

        setup = load_setup('IbidiEmulator')
        for entry in setup['fluid']['reservoirs']:
            if entry['id'] == 3:
                entry['valve_pos'] = {'ibidi': [1, 3], 1: 'in'}
        hamilton, tubing = assemble_hamilton_config(setup, {
            'reservoir_names': {1: 'imager1', 3: 'shared', 5: 'buffer'},
            'special_names': {'flushbuffer_a': 5},
        })
        with emu.patch_serial(), emu.patch_ibidi_serial() as fake:
            ha.connect(hamilton['interface']['COM'],
                       hamilton['interface']['baud'])
            la = ha.LegacyArchitecture(hamilton, tubing)
        la._set_valves(3)
        self.assertEqual(
            [i for i, s in enumerate(fake.channels, 1) if s], [1, 3])
        # a single-channel reservoir still closes the extra channel
        la._set_valves(1)
        self.assertEqual(
            [i for i, s in enumerate(fake.channels, 1) if s], [1])

    def test_set_valves_routes_through_multiplexer(self):
        la, fake = self._build()
        # The fake serial persists on the constructed multiplexer after the
        # patch context exits, so _set_valves drives it directly. _set_valves
        # opens the channel matching the reservoir id and sets the pump input
        # position; verify via the fake's channel state.
        la._set_valves(2)
        self.assertEqual(
            [i for i, s in enumerate(fake.channels, 1) if s], [2])
        la._set_valves(1)
        self.assertEqual(
            [i for i, s in enumerate(fake.channels, 1) if s], [1])


if __name__ == '__main__':
    unittest.main()
