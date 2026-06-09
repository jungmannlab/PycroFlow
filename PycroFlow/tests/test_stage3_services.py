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
        with patch('pycromanager.Core',
                   return_value=MagicMock(name='Core')) as core_cls:
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
        self.assertEqual(transitions, [
            (ExperimentState.IDLE, ExperimentState.LOADED),
        ])

    def test_log_observer_fires(self):
        from PycroFlow.examples.demo_protocols import protocol
        svc = ExperimentService()
        lines = []
        svc.add_log_observer(lambda msg: lines.append(msg))
        svc.load_protocol(protocol)
        self.assertEqual(len(lines), 1)
        self.assertIn('loaded', lines[0])

    def test_observer_exception_does_not_break_service(self):
        from PycroFlow.examples.demo_protocols import protocol
        svc = ExperimentService()
        svc.add_state_observer(
            lambda o, n: (_ for _ in ()).throw(RuntimeError('boom')))
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
            fluid_system=f, imaging_system=i, illumination_system=lum)
        self.assertIs(svc._fluid_system, f)
        self.assertIs(svc._imaging_system, i)
        self.assertIs(svc._illumination_system, lum)

    def test_attach_systems_feeds_orchestrator(self):
        from PycroFlow.examples.demo_protocols import protocol
        svc = ExperimentService()
        fluid = MagicMock(name='fluid_system')
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
        fluid = MagicMock(name='fluid')
        svc.attach_systems(fluid_system=fluid)
        with patch('PycroFlow.orchestration.core.ProtocolOrchestrator.'
                   'start_orchestration'), \
                patch('PycroFlow.orchestration.core.ProtocolOrchestrator.'
                      'start_protocol'):
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
        result = svc.manual_pump('pump_a', vol=100)
        self.assertEqual(result, 42)
        fluid._pump.assert_called_once_with(fluid.pump_a, vol=100)

    def test_connection_states(self):
        svc = SystemService()
        self.assertEqual(
            svc.connection_states(),
            {'fluid': False, 'imaging': False, 'illumination': False})
        svc.imaging_system = object()
        self.assertTrue(svc.connection_states()['imaging'])

    def test_connect_imaging_builds_and_stores(self):
        sentinel = object()
        with patch('PycroFlow.imaging.ImagingSystem', return_value=sentinel):
            svc = SystemService()
            result = svc.connect_imaging({'pfs_pars': {}})
        self.assertIs(result, sentinel)
        self.assertIs(svc.imaging_system, sentinel)

    def test_connect_illumination_builds_and_stores(self):
        sentinel = object()
        with patch('PycroFlow.illumination.IlluminationSystem',
                   return_value=sentinel):
            svc = SystemService()
            result = svc.connect_illumination()
        self.assertIs(result, sentinel)
        self.assertIs(svc.illumination_system, sentinel)

    def test_connect_fluid_requires_setup(self):
        svc = SystemService()
        with self.assertRaises(RuntimeError):
            svc.connect_fluid({'settings': {'reservoir_names': {}}})

    def test_connect_fluid_emulated_builds_legacy(self):
        # The Emulator setup connects the real LegacyArchitecture over the
        # fake serial wire emulator; design parameters are seeded so manual
        # ops work immediately.
        from PycroFlow.fluid.legacy import LegacyArchitecture
        svc = SystemService()
        svc.load_setup('Emulator')
        fluid = {
            'parameters': {'max_velocity': 200, 'clean_velocity': 200},
            'settings': {'reservoir_names': {1: 'R1', 7: 'C+'},
                         'special_names': {'flushbuffer_a': 7}},
        }
        fs = svc.connect_fluid(fluid)
        self.assertIsInstance(fs, LegacyArchitecture)
        self.assertIs(svc.fluid_system, fs)
        self.assertEqual(fs.parameters['max_velocity'], 200)

    def test_load_setup_and_monet_name(self):
        svc = SystemService()
        svc.load_setup('Emulator')
        self.assertTrue(svc.is_emulated())
        self.assertEqual(svc.get_monet_setup(), 'Emulator')


if __name__ == '__main__':
    unittest.main()
