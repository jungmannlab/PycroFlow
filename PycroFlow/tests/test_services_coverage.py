"""Additional coverage for the services layer (lifecycle, manual control,
MM Core ownership) beyond the happy paths in test_stage3_services."""
import os
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from PycroFlow.services import (
    ExperimentService, ExperimentState, SystemService, mm_core,
)
from PycroFlow.services import get_core, get_studio, reset_core


# ---------------------------------------------------------------------------
# ExperimentService lifecycle
# ---------------------------------------------------------------------------

_MINIMAL = {'fluid': {'protocol_entries': []}}


def _loaded_service_with_mock_orchestrator():
    svc = ExperimentService()
    svc.load_protocol(_MINIMAL)          # builds a real (unstarted) orchestrator
    svc._orchestrator = MagicMock(name='orchestrator')  # swap in a stub
    return svc


class ExperimentLifecycleTest(unittest.TestCase):
    def test_start_from_loaded_runs(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc.start()
        self.assertEqual(svc.state, ExperimentState.RUNNING)
        svc._orchestrator.start_orchestration.assert_called_once()
        svc._orchestrator.start_protocol.assert_called_once()

    def test_start_forwards_system_steps(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc.start(system_steps={'fluid': 3})
        svc._orchestrator.start_protocol.assert_called_once_with({'fluid': 3})

    def test_pause_resume_transitions(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc.start()
        svc.pause()
        self.assertEqual(svc.state, ExperimentState.PAUSED)
        svc._orchestrator.pause_protocol.assert_called_once()
        svc.resume()
        self.assertEqual(svc.state, ExperimentState.RUNNING)
        svc._orchestrator.resume_protocol.assert_called_once()

    def test_abort_transitions(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc.start()
        svc.abort()
        self.assertEqual(svc.state, ExperimentState.ABORTED)
        svc._orchestrator.abort_protocol.assert_called_once()

    def test_end_transitions(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc.start()
        svc.end()
        self.assertEqual(svc.state, ExperimentState.FINISHED)
        svc._orchestrator.end_orchestration.assert_called_once()

    def test_end_without_orchestrator_noop(self):
        ExperimentService().end()  # must not raise

    def test_is_finished_delegates(self):
        svc = _loaded_service_with_mock_orchestrator()
        svc._orchestrator.poll_protocol_finished.return_value = True
        self.assertTrue(svc.is_finished())

    def test_is_finished_false_without_orchestrator(self):
        self.assertFalse(ExperimentService().is_finished())

    def test_start_without_protocol_raises(self):
        with self.assertRaises(RuntimeError):
            ExperimentService().start()

    def test_pause_without_protocol_raises(self):
        with self.assertRaises(RuntimeError):
            ExperimentService().pause()

    def test_load_from_yaml(self):
        svc = ExperimentService()
        fd, path = tempfile.mkstemp(suffix='.yaml')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write("fluid:\n  protocol_entries: []\n")
            svc.load_protocol_from_yaml(path)
        finally:
            os.unlink(path)
        self.assertEqual(svc.state, ExperimentState.LOADED)
        self.assertEqual(svc.protocol, {'fluid': {'protocol_entries': []}})


# ---------------------------------------------------------------------------
# SystemService manual control
# ---------------------------------------------------------------------------

class SystemServiceTest(unittest.TestCase):
    def test_fluid_commands_delegate(self):
        fluid = MagicMock()
        svc = SystemService(fluid_system=fluid)
        svc.fill_tubings()
        svc.clean_tubings()
        svc.deliver_fluid(2, 150)
        fluid.fill_tubings.assert_called_once()
        fluid.clean_tubings.assert_called_once()
        fluid.deliver_fluid.assert_called_once_with(2, 150)

    def test_illumination_commands_delegate(self):
        illu = MagicMock()
        svc = SystemService(illumination_system=illu)
        svc.set_laser(561)
        svc.set_laser_enabled(561, enabled=False)
        svc.set_sample_power(40, warmup_delay=0)
        illu.set_laser.assert_called_once_with(561)
        illu.set_laser_enabled.assert_called_once_with(561, enabled=False)
        illu.set_sample_power.assert_called_once_with(40, 0)

    def test_illumination_require_raises_when_absent(self):
        with self.assertRaises(RuntimeError):
            SystemService().set_laser(488)

    def test_close_imaging_calls_close(self):
        imaging = MagicMock()
        svc = SystemService(imaging_system=imaging)
        svc.close_imaging()
        imaging.close.assert_called_once()

    def test_close_imaging_noop_without_system(self):
        SystemService().close_imaging()  # must not raise

    def test_stop_all_moves_swallows_errors(self):
        fluid = MagicMock()
        fluid.stop_all_moves.side_effect = RuntimeError('boom')
        # Should log and not propagate.
        SystemService(fluid_system=fluid).stop_all_moves()

    def test_manual_pump_no_pump_method(self):
        # fluid_system without a _pump attribute.
        fluid = types.SimpleNamespace(pump_a=object())
        svc = SystemService(fluid_system=fluid)
        with self.assertRaises(RuntimeError):
            svc.manual_pump('pump_a')

    def test_manual_pump_unknown_pump(self):
        # has _pump but no such pump attribute.
        fluid = types.SimpleNamespace(_pump=lambda *a, **k: 'ok')
        svc = SystemService(fluid_system=fluid)
        with self.assertRaises(KeyError):
            svc.manual_pump('pump_zzz')

    def test_close_is_idempotent_and_calls_through(self):
        fluid = MagicMock()
        imaging = MagicMock()
        svc = SystemService(fluid_system=fluid, imaging_system=imaging)
        svc.close()
        fluid.stop_all_moves.assert_called_once()
        imaging.close.assert_called_once()


# ---------------------------------------------------------------------------
# mm_core ownership
# ---------------------------------------------------------------------------

class MmCoreTest(unittest.TestCase):
    def setUp(self):
        reset_core()

    def tearDown(self):
        reset_core()

    def test_get_studio_caches(self):
        with patch('pycromanager.Studio',
                   return_value=MagicMock(name='Studio')) as studio_cls:
            a = get_studio()
            b = get_studio()
        self.assertIs(a, b)
        studio_cls.assert_called_once()
        self.assertTrue(mm_core.is_initialized())

    def test_reset_core_clears_cache(self):
        with patch('pycromanager.Core', return_value=MagicMock()):
            get_core()
        self.assertTrue(mm_core.is_initialized())
        reset_core()
        self.assertFalse(mm_core.is_initialized())

    def test_share_with_monet_sets_pycrocore(self):
        # monet is mocked at import; share_with_monet should assign our Core
        # onto monet.beampath.pycrocore.
        import monet.beampath as mbp
        with patch('pycromanager.Core', return_value=MagicMock(name='Core')):
            mm_core.share_with_monet()
            self.assertIs(mbp.pycrocore, get_core())


if __name__ == '__main__':
    unittest.main()
