"""Tests for the Stage-3 HAL + services layer."""

import unittest
from unittest.mock import MagicMock, patch

from PycroFlow.hal import Pump, Valve, SpillSensor
from PycroFlow.services import (
    ExperimentService,
    ExperimentState,
    SystemService,
    get_core,
    reset_core,
)
from PycroFlow.services import mm_core


def _fake_monet(configs):
    """Patch ``sys.modules['monet']`` with a stub exposing ``CONFIGS``.

    The real monet (if installed at all) knows nothing of this repo's setup
    names, so the illumination tests supply their own config registry.
    """
    import sys
    import types

    fake = types.ModuleType('monet')
    fake.CONFIGS = configs
    return patch.dict(sys.modules, {'monet': fake})


class TestHALAbcsRegisterConcrete(unittest.TestCase):

    def test_hamilton_pump_is_hal_pump(self):
        from PycroFlow.hamilton_components import Pump as HamiltonPump

        self.assertTrue(issubclass(HamiltonPump, Pump))

    def test_hamilton_valve_is_hal_valve(self):
        from PycroFlow.hamilton_components import Valve as HamiltonValve

        self.assertTrue(issubclass(HamiltonValve, Valve))

    def test_arduino_sensor_is_hal_spill_sensor(self):
        from PycroFlow.spill_sensor_arduino import ArduinoSensorInterface

        self.assertTrue(issubclass(ArduinoSensorInterface, SpillSensor))


class TestMmCore(unittest.TestCase):

    def setUp(self):
        # Each test starts with a clean cache.
        reset_core()

    def tearDown(self):
        reset_core()

    def test_lazy_init(self):
        self.assertFalse(mm_core.is_initialized())

    def test_get_core_caches(self):
        # When real pycromanager is installed it tries an actual connection;
        # patch the Core constructor so the test is portable.
        with patch(
            "pycromanager.Core", return_value=MagicMock(name="Core")
        ) as core_cls:
            a = get_core()
            b = get_core()
        self.assertIs(a, b)
        self.assertTrue(mm_core.is_initialized())
        core_cls.assert_called_once()


class TestExperimentService(unittest.TestCase):

    def test_initial_state_is_idle(self):
        svc = ExperimentService()
        self.assertEqual(svc.state, ExperimentState.IDLE)
        self.assertIsNone(svc.orchestrator)
        self.assertIsNone(svc.protocol)

    def test_load_protocol_transitions_to_loaded(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        svc.load_protocol(protocol)
        self.assertEqual(svc.state, ExperimentState.LOADED)
        self.assertIsNotNone(svc.orchestrator)
        self.assertIs(svc.protocol, protocol)

    def test_state_observer_fires(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        transitions = []
        svc.add_state_observer(lambda o, n: transitions.append((o, n)))
        svc.load_protocol(protocol)
        self.assertEqual(
            transitions,
            [
                (ExperimentState.IDLE, ExperimentState.LOADED),
            ],
        )

    def test_log_observer_fires(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        lines = []
        svc.add_log_observer(lambda msg: lines.append(msg))
        svc.load_protocol(protocol)
        self.assertEqual(len(lines), 1)
        self.assertIn("loaded", lines[0])

    def test_observer_exception_does_not_break_service(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        svc.add_state_observer(
            lambda o, n: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        # Must not raise.
        svc.load_protocol(protocol)
        self.assertEqual(svc.state, ExperimentState.LOADED)

    def test_load_protocol_while_running_rejects(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        svc.load_protocol(protocol)
        # Hand-walk into a forbidden state to verify the guard.
        svc._set_state(ExperimentState.RUNNING)
        with self.assertRaises(RuntimeError):
            svc.load_protocol(protocol)

    def test_abort_idempotent_without_orchestrator(self):
        # abort() with no protocol loaded must not raise.
        ExperimentService().abort()

    def test_attach_systems_sets_refs(self):
        svc = ExperimentService()
        f, i, lum = object(), object(), object()
        svc.attach_systems(
            fluid_system=f, imaging_system=i, illumination_system=lum
        )
        self.assertIs(svc._fluid_system, f)
        self.assertIs(svc._imaging_system, i)
        self.assertIs(svc._illumination_system, lum)

    def test_attach_systems_feeds_orchestrator(self):
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        fluid = MagicMock(name="fluid_system")
        svc.attach_systems(fluid_system=fluid)
        svc.load_protocol(protocol)
        self.assertIs(svc.orchestrator.fluid_system, fluid)

    def test_attach_systems_rejected_while_active(self):
        svc = ExperimentService()
        svc._set_state(ExperimentState.RUNNING)
        with self.assertRaises(RuntimeError):
            svc.attach_systems()

    def test_start_rebuilds_with_systems_attached_after_load(self):
        # Regression: connecting hardware AFTER load/translate must still
        # feed the orchestrator — start() rebuilds it. Otherwise handlers
        # see system=None and the protocol finishes immediately.
        from PycroFlow.examples.demo_protocols import protocol

        svc = ExperimentService()
        svc.load_protocol(protocol)
        self.assertIsNone(svc.orchestrator.fluid_system)
        fluid = MagicMock(name="fluid")
        svc.attach_systems(fluid_system=fluid)
        with (
            patch(
                "PycroFlow.orchestration.core.ProtocolOrchestrator."
                "start_orchestration"
            ),
            patch(
                "PycroFlow.orchestration.core.ProtocolOrchestrator."
                "start_protocol"
            ),
        ):
            svc.start()
        self.assertIs(svc.orchestrator.fluid_system, fluid)
        self.assertEqual(svc.state, ExperimentState.RUNNING)


class TestSystemService(unittest.TestCase):

    def test_require_raises_when_subsystem_none(self):
        svc = SystemService()
        with self.assertRaises(RuntimeError):
            svc.fill_tubings()

    def test_stop_all_moves_is_safe_without_fluid(self):
        SystemService().stop_all_moves()

    def test_close_idempotent(self):
        svc = SystemService()
        svc.close()  # nothing to close
        svc.close()  # still nothing — must not raise

    def test_manual_pump_delegates(self):
        fluid = MagicMock()
        fluid.pump_a = MagicMock()
        fluid._pump.return_value = 42
        svc = SystemService(fluid_system=fluid)
        result = svc.manual_pump("pump_a", vol=100)
        self.assertEqual(result, 42)
        fluid._pump.assert_called_once_with(fluid.pump_a, vol=100)

    def test_connection_states(self):
        svc = SystemService()
        self.assertEqual(
            svc.connection_states(),
            {"fluid": False, "imaging": False, "illumination": False},
        )
        svc.imaging_system = object()
        self.assertTrue(svc.connection_states()["imaging"])

    def test_connect_imaging_builds_and_stores(self):
        sentinel = object()
        with patch("PycroFlow.imaging.ImagingSystem", return_value=sentinel):
            svc = SystemService()
            result = svc.connect_imaging({"pfs_pars": {}})
        self.assertIs(result, sentinel)
        self.assertIs(svc.imaging_system, sentinel)

    def test_connect_illumination_builds_and_stores(self):
        sentinel = object()
        svc = SystemService()
        svc.load_setup('Mercury')
        with patch('PycroFlow.illumination.IlluminationSystem',
                   return_value=sentinel), _fake_monet({'Mercury': {}}):
            result = svc.connect_illumination()
        self.assertIs(result, sentinel)
        self.assertIs(svc.illumination_system, sentinel)

    def test_connect_illumination_without_monet_config_raises(self):
        # No setup loaded -> nothing declares a monet config.
        svc = SystemService()
        with self.assertRaises(RuntimeError):
            svc.connect_illumination()

    def test_connect_illumination_unknown_monet_config_raises(self):
        # The setup names a monet config that monet does not know: fail at
        # connect (so the GUI shows "not connected") rather than mid-run.
        svc = SystemService()
        svc.load_setup('Mercury')
        with _fake_monet({'SomeOtherScope': {}}):
            with self.assertRaises(KeyError):
                svc.connect_illumination()
        self.assertIsNone(svc.illumination_system)

    def test_monet_config_may_differ_from_setup_name(self):
        # A fluidics setup (Ibidi) running on another microscope (Mercury)
        # illuminates with that microscope's monet config.
        svc = SystemService()
        svc.load_setup('Ibidi')
        self.assertEqual(svc.setup_name(), 'Ibidi')
        self.assertEqual(svc.get_monet_setup(), 'Mercury')
        with _fake_monet({'Mercury': {'lasers': {560: {}, 488: {}}}}):
            self.assertEqual(svc.laser_options(), [488, 560])
            with patch('PycroFlow.illumination.IlluminationSystem') as IS:
                svc.connect_illumination()
        IS.assert_called_once_with(setup='Mercury')

    def test_laser_options_from_monet_config(self):
        svc = SystemService()
        svc.load_setup('Mercury')
        with _fake_monet({'Mercury': {'lasers': {640: {}, 488: {}, 561: {}}}}):
            self.assertEqual(svc.laser_options(), [488, 561, 640])

    def test_laser_options_string_keys_become_ints(self):
        # monet config keys may be YAML strings; the design's laser is an int.
        svc = SystemService()
        svc.load_setup('Mercury')
        with _fake_monet({'Mercury': {'lasers': {'640': {}, '488': {}}}}):
            self.assertEqual(svc.laser_options(), [488, 640])

    def test_laser_options_from_single_laser_config(self):
        # A single-laser monet config names its line in index[LASER_TAG]
        # rather than in a 'lasers' mapping.
        svc = SystemService()
        svc.load_setup('Mercury')
        with _fake_monet({'Mercury': {'index': {'wavelength [nm]': 561}}}):
            self.assertEqual(svc.laser_options(), [561])

    def test_laser_options_empty_when_config_names_no_laser(self):
        svc = SystemService()
        svc.load_setup('Mercury')
        with _fake_monet({'Mercury': {'powermeter': {}}}):
            self.assertEqual(svc.laser_options(), [])

    def test_laser_options_empty_without_real_config(self):
        # Emulator has no monet config (and monet may be mocked) -> empty.
        svc = SystemService()
        svc.load_setup("Emulator")
        self.assertEqual(svc.laser_options(), [])
        # No setup at all -> empty too.
        self.assertEqual(SystemService().laser_options(), [])

    def test_connect_illumination_passes_monet_setup(self):
        # The monet config name is taken from the chosen microscope setup.
        svc = SystemService()
        svc.load_setup('Mercury')   # non-emulated -> real illumination path
        with patch('PycroFlow.illumination.IlluminationSystem') as IS, \
                _fake_monet({'Mercury': {}}):
            svc.connect_illumination()
        IS.assert_called_once_with(setup="Mercury")

    def test_describe_route_names_the_valves_and_channels(self):
        # The manual controls show what routing to a reservoir will do.
        svc = SystemService()
        setup = svc.load_setup('IbidiEmulator')
        setup['fluid']['reservoirs'] = [
            {'id': 8, 'valve_pos': {'ibidi': [1, 6, 7, 8], 1: 'in'}}]
        text = svc.describe_reservoir_route(8)
        self.assertIn('ibidi multiplexer opens channels 1, 6, 7, 8', text)
        self.assertIn('all others closed', text)   # unlike a rotary valve
        self.assertIn('pump_a', text)
        # Without a fluid system the design cannot be using it.
        self.assertIn('not used by the design', text)

    def test_describe_route_for_hamilton_valves(self):
        svc = SystemService()
        svc.load_setup('Mercury')
        text = svc.describe_reservoir_route(14)
        self.assertIn('MVP valve 3', text)
        self.assertIn('MVP valve 5', text)

    def test_describe_route_unwired_and_no_setup(self):
        svc = SystemService()
        self.assertIn('No setup', svc.describe_reservoir_route(1))
        svc.load_setup('Mercury')
        self.assertIn('not wired', svc.describe_reservoir_route(999))

    def test_reservoir_route_returns_the_valve_map(self):
        svc = SystemService()
        setup = svc.load_setup('IbidiEmulator')
        setup['fluid']['reservoirs'] = [
            {'id': 3, 'valve_pos': {'ibidi': [1, 3], 1: 'in'}}]
        self.assertEqual(svc.reservoir_route(3)['ibidi'], [1, 3])
        self.assertEqual(svc.reservoir_route(4), {})

    def test_connect_fluid_requires_setup(self):
        svc = SystemService()
        with self.assertRaises(RuntimeError):
            svc.connect_fluid({"settings": {"reservoir_names": {}}})

    def test_connect_fluid_emulated_builds_legacy(self):
        # The Emulator setup connects the real LegacyArchitecture over the
        # fake serial wire emulator; design parameters are seeded so manual
        # ops work immediately.
        from PycroFlow.fluid.legacy import LegacyArchitecture

        svc = SystemService()
        svc.load_setup("Emulator")
        fluid = {
            "parameters": {"max_velocity": 200, "clean_velocity": 200},
            "settings": {
                "reservoir_names": {1: "R1", 7: "C+"},
                "special_names": {"flushbuffer_a": 7},
            },
        }
        fs = svc.connect_fluid(fluid)
        self.assertIsInstance(fs, LegacyArchitecture)
        self.assertIs(svc.fluid_system, fs)
        self.assertEqual(fs.parameters["max_velocity"], 200)

    def test_load_setup_and_monet_name(self):
        svc = SystemService()
        svc.load_setup("Emulator")
        self.assertTrue(svc.is_emulated())
        self.assertEqual(svc.get_monet_setup(), "Emulator")

    def test_has_multiplexer(self):
        svc = SystemService()
        self.assertFalse(svc.has_multiplexer())   # no setup loaded
        svc.load_setup('IbidiEmulator')
        self.assertTrue(svc.has_multiplexer())
        svc.load_setup('Emulator')
        self.assertFalse(svc.has_multiplexer())

    def test_close_all_valves_closes_every_channel(self):
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1', 2: 'R2'},
                         'special_names': {'flushbuffer_a': 7}},
        })
        mux = svc.fluid_system.multiplexer
        mux.select([1, 2, 3])
        self.assertTrue(any(mux.channel_states))
        svc.close_all_valves()
        self.assertFalse(any(mux.channel_states))

    def test_close_all_valves_without_multiplexer_raises(self):
        svc = SystemService()
        svc.load_setup('Emulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1'},
                         'special_names': {'flushbuffer_a': 1}},
        })
        with self.assertRaises(RuntimeError):
            svc.close_all_valves()

    def test_toggle_multiplexer_channel_flips_raw_state(self):
        # A raw manual override: toggling one channel does not disturb others
        # and ignores reservoir routing entirely.
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1', 2: 'R2'},
                         'special_names': {}},
        })
        mux = svc.fluid_system.multiplexer
        self.assertFalse(mux.channel_states[4])
        self.assertTrue(svc.toggle_multiplexer_channel(5))   # closed -> open
        self.assertTrue(mux.channel_states[4])
        self.assertFalse(svc.toggle_multiplexer_channel(5))  # open -> closed
        self.assertFalse(mux.channel_states[4])

    def test_toggle_multiplexer_without_mux_raises(self):
        svc = SystemService()
        svc.load_setup('Emulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1'},
                         'special_names': {'flushbuffer_a': 1}},
        })
        with self.assertRaises(RuntimeError):
            svc.toggle_multiplexer_channel(1)

    def test_toggle_pump_valve_flips_in_out(self):
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1'},
                         'special_names': {}},
        })
        self.assertEqual(svc.toggle_pump_valve('pump_a'), 'in')   # None -> in
        self.assertEqual(svc.fluid_system.pump_a.valve_pos, 'in')
        self.assertEqual(svc.toggle_pump_valve('pump_a'), 'out')
        self.assertEqual(svc.toggle_pump_valve('pump_a'), 'in')
        with self.assertRaises(KeyError):
            svc.toggle_pump_valve('pump_nope')

    def test_fluid_topology_reads_grid_and_taps(self):
        # The live-schematic topology is read straight from the setup: the
        # ibidi grid geometry, the pump-wired port, and each port's tap.
        svc = SystemService()
        svc.load_setup('Ibidi')
        topo = svc.fluid_topology()
        mux = topo['multiplexer']
        self.assertEqual((mux['cols'], mux['rows']), (6, 4))
        self.assertEqual(mux['channels'], 24)
        self.assertEqual(mux['pump_channel'], 1)
        # R8 is tapped at its leaf channel (last in the route [1, 6, 12, 8]).
        self.assertEqual(mux['ports'][8]['reservoir'], 8)
        # Ports 6/12 are shared bridges on the way to R8..R24.
        self.assertIn(8, mux['ports'][6]['used_by'])
        self.assertIn(8, mux['ports'][12]['used_by'])
        # The (meandered) tubing path to R8 is 1->6->12->8.
        for edge in [(1, 6), (6, 12), (12, 8)]:
            self.assertIn(edge, mux['edges'])
        # Each reservoir's full ordered route is exposed for path highlight.
        self.assertEqual(mux['routes'][8], [1, 6, 12, 8])
        self.assertEqual(
            mux['routes'][23], [1, 6, 12, 7, 13, 18, 24, 23])
        # The old meander-numbering leftover (a direct 6->7 link) is gone.
        self.assertNotIn((6, 7), mux['edges'])
        self.assertNotIn((7, 6), mux['edges'])
        self.assertTrue(topo['pumps']['pump_a'])
        self.assertTrue(topo['pumps']['pump_out'])
        self.assertIsNone(topo['valves'])  # ibidi setup has no rotary valves

    def test_fluid_topology_describes_chained_mvp_valves(self):
        # A Hamilton MVP setup yields a `valves` topology (not `multiplexer`):
        # the root valve taps its own reservoirs and bridges to the next valve.
        svc = SystemService()
        svc.load_setup('Mercury')
        topo = svc.fluid_topology()
        self.assertIsNone(topo['multiplexer'])
        vt = topo['valves']
        v3, v5 = vt['valves']
        self.assertEqual((v3['address'], v3['index'], v3['ports']), (3, 0, 8))
        self.assertEqual((v5['address'], v5['index'], v5['ports']), (5, 1, 8))
        # V3 ports 2..8 tap reservoirs 1..7; port 1 bridges to valve 5.
        self.assertEqual(v3['taps'][2], 1)
        self.assertEqual(v3['taps'][8], 7)
        self.assertEqual(v3['bridges'], {1: 5})
        # V5 taps reservoirs 14..21 and bridges nowhere (it is the leaf valve).
        self.assertEqual(v5['taps'][8], 21)
        self.assertEqual(v5['bridges'], {})
        # Routes run root -> leaf as (valve, port) pairs.
        self.assertEqual(vt['routes'][1], [(3, 2)])
        self.assertEqual(vt['routes'][21], [(3, 1), (5, 8)])
        # Mercury wires a flush_waste sink (pump_a -> flush_waste).
        self.assertTrue(topo['flush_waste'])

    def test_fluid_waste_labels_track_extraction_and_flush(self):
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200, 'extractionfactor': 2},
            'settings': {'reservoir_names': {1: 'Imager 1'},
                         'special_names': {}},
        })
        svc.fluid_system._assign_protocol({
            'parameters': {'max_velocity': 200, 'extractionfactor': 2},
            'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 300},
                {'$type': 'pump_out', 'volume': 100},
            ],
        })
        waste = svc.fluid_waste_labels()
        # Extraction waste planned = 2*(300+100) = 800 µl; flush_waste wired.
        self.assertEqual(waste['waste']['total_vol'], 800.0)
        self.assertEqual(waste['waste']['used_vol'], 0.0)
        self.assertIn('flush_waste', waste)
        # Recording flush volume backfills its total from the amount received.
        svc.fluid_system._record_waste('flush_waste', 650)
        waste = svc.fluid_waste_labels()
        self.assertEqual(waste['flush_waste']['used_vol'], 650.0)
        self.assertEqual(waste['flush_waste']['total_vol'], 650.0)

    def test_fluid_reservoir_labels_names_and_usage(self):
        # After connecting, the labels expose the design's names and mark
        # which setup-wired reservoirs the design actually uses.
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'Imager 1', 3: 'Buffer'},
                         'special_names': {}},
        })
        labels = svc.fluid_reservoir_labels()
        self.assertEqual(labels[1]['name'], 'Imager 1')
        self.assertTrue(labels[1]['used'])
        self.assertEqual(labels[3]['name'], 'Buffer')
        self.assertTrue(labels[3]['used'])
        # A wired-but-unused reservoir is present, named None, marked unused.
        self.assertEqual(labels[2]['name'], None)
        self.assertFalse(labels[2]['used'])
        # No protocol assigned yet -> zero planned/used volume.
        self.assertEqual(labels[1]['used_vol'], 0.0)
        self.assertEqual(labels[1]['total_vol'], 0.0)
        # Not connected -> empty (schematic then stays neutral).
        self.assertEqual(SystemService().fluid_reservoir_labels(), {})

    def test_reservoir_labels_track_planned_and_pumped_volume(self):
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'Imager 1', 2: 'Buffer'},
                         'special_names': {}},
        })
        # Assigning a protocol sets the per-reservoir planned totals.
        svc.fluid_system._assign_protocol({
            'parameters': {'max_velocity': 200},
            'protocol_entries': [
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 300},
                {'$type': 'inject', 'reservoir_id': 1, 'volume': 200},
                {'$type': 'inject', 'reservoir_id': 2, 'volume': 1000},
            ],
        })
        labels = svc.fluid_reservoir_labels()
        self.assertEqual(labels[1]['total_vol'], 500.0)
        self.assertEqual(labels[2]['total_vol'], 1000.0)
        self.assertEqual(labels[1]['used_vol'], 0.0)
        # Recording pumped volume shows up as used.
        svc.fluid_system._record_used(1, 300)
        self.assertEqual(svc.fluid_reservoir_labels()[1]['used_vol'], 300.0)

    def test_toggle_and_labels_survive_reservoir_resync(self):
        # sync_fluid_reservoirs re-applies a changed design live (no serial
        # reconnect) and refreshes the schematic's names/usage.
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {'reservoir_names': {1: 'A'}, 'special_names': {}},
        })
        self.assertFalse(svc.fluid_reservoir_labels()[2]['used'])
        # Edit the design to add reservoir 2, then re-sync (as Translate does).
        svc.sync_fluid_reservoirs({'settings': {
            'reservoir_names': {1: 'A', 2: 'B'}, 'special_names': {}}})
        labels = svc.fluid_reservoir_labels()
        self.assertTrue(labels[2]['used'])
        self.assertEqual(labels[2]['name'], 'B')

    def test_fluid_topology_none_without_setup_and_no_mux(self):
        svc = SystemService()
        self.assertIsNone(svc.fluid_topology())
        svc.load_setup('Mercury')   # Hamilton MVP, no ibidi multiplexer
        topo = svc.fluid_topology()
        self.assertIsNone(topo['multiplexer'])

    def test_fluid_state_reflects_routing_without_serial_poll(self):
        # fluid_state() reads cached driver attributes only (no bus traffic),
        # so it is safe to poll live — here it mirrors a manual route.
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200},
            'settings': {
                'reservoir_names': {i: 'R%d' % i for i in range(1, 4)},
                'special_names': {}},
        })
        self.assertIsNone(SystemService().fluid_state())   # not connected
        svc.set_valves(3)
        state = svc.fluid_state()
        opened = [i + 1 for i, v in enumerate(state['multiplexer']['open'])
                  if v]
        self.assertEqual(opened, [3])
        self.assertEqual(state['pump_a']['valve'], 'in')
        self.assertEqual(state['pump_a']['capacity'], 500.0)
        self.assertIsNotNone(state['pump_out'])

    def test_fill_tubings_without_flushbuffer_skips_final_flush(self):
        # A design that defines no 'flushbuffer_a' must not crash fill: the
        # post-fill flushbuffer step is skipped rather than dead-ending in
        # the tubing lookup for an unrouted 'flushbuffer_a'.
        svc = SystemService()
        svc.load_setup('IbidiEmulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200, 'clean_velocity': 200,
                           'clean_delay': 0},
            'settings': {'reservoir_names': {1: 'R1', 2: 'R2'},
                         'special_names': {}},
        })
        svc.fill_tubings()   # must not raise


if __name__ == '__main__':
    unittest.main()
