"""Coverage for the PycroFlowInteractive CLI (frontend_cli).

The CLI is a cmd.Cmd REPL whose do_* commands route through the service layer
and the orchestrator. We construct it with auto-loading disabled, then drive
the commands against mocks, asserting delegation, argument parsing, and the
"orchestration not started" guards.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from PycroFlow.frontend_cli import PycroFlowInteractive


def _cli():
    # __init__ scans the cwd and auto-loads configs; disable that for tests.
    # configure_logging=False keeps the test run from writing log files into
    # the repo / reconfiguring loguru sinks.
    with patch('PycroFlow.frontend_cli.os.listdir', return_value=[]):
        return PycroFlowInteractive(configure_logging=False)


def _cli_with_orchestrator():
    cli = _cli()
    cli.orchestrator = MagicMock(name='orchestrator')
    cli.fluid_system = MagicMock(name='fluid_system')
    cli.illumination_system = MagicMock(name='illumination_system')
    return cli


class GuardTest(unittest.TestCase):
    """Commands print a hint and return when orchestration isn't started."""

    def _assert_guarded(self, method, *args):
        cli = _cli()  # orchestrator is None
        buf = io.StringIO()
        with redirect_stdout(buf):
            method(cli, *args)
        self.assertIn('Start orchestration first', buf.getvalue())

    def test_guards(self):
        self._assert_guarded(PycroFlowInteractive.do_start_protocol, '')
        self._assert_guarded(PycroFlowInteractive.do_pause_protocol, '')
        self._assert_guarded(PycroFlowInteractive.do_resume_protocol, '')
        self._assert_guarded(PycroFlowInteractive.do_abort_protocol, '')
        self._assert_guarded(PycroFlowInteractive.do_is_protocol_done, '')
        self._assert_guarded(PycroFlowInteractive.do_get_protocol_iter, '')
        self._assert_guarded(PycroFlowInteractive.do_set_valves, '0')
        self._assert_guarded(PycroFlowInteractive.do_power, '20')

    def test_abort_orchestration_guard(self):
        cli = _cli()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.do_abort_orchestration('')
        self.assertIn('Start orchestration first', buf.getvalue())

    def test_start_orchestration_without_protocol(self):
        cli = _cli()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.do_start_orchestration('')
        self.assertIn('Load the protocol first', buf.getvalue())


class ProtocolControlTest(unittest.TestCase):
    def test_start_protocol_parses_entries(self):
        cli = _cli_with_orchestrator()
        cli.do_start_protocol('fluid: 5, img: 2')
        cli.orchestrator.start_protocol.assert_called_once_with(
            {'fluid': 4, 'img': 1})

    def test_start_protocol_no_entries(self):
        cli = _cli_with_orchestrator()
        cli.do_start_protocol('')
        cli.orchestrator.start_protocol.assert_called_once_with({})

    def test_pause_resume_abort_delegate(self):
        cli = _cli_with_orchestrator()
        cli.do_pause_protocol('')
        cli.do_resume_protocol('')
        cli.do_abort_protocol('')
        cli.orchestrator.pause_protocol.assert_called_once()
        cli.orchestrator.resume_protocol.assert_called_once()
        cli.orchestrator.abort_protocol.assert_called_once()

    def test_abort_orchestration_delegates(self):
        cli = _cli_with_orchestrator()
        cli.do_abort_orchestration('')
        cli.orchestrator.abort_orchestration.assert_called_once()

    def test_is_protocol_done_prints(self):
        cli = _cli_with_orchestrator()
        cli.orchestrator.poll_protocol_finished.return_value = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.do_is_protocol_done('')
        self.assertIn('True', buf.getvalue())

    def test_get_protocol_iter_queries_all_systems(self):
        cli = _cli_with_orchestrator()
        cli.do_get_protocol_iter('')
        self.assertEqual(
            cli.orchestrator.execute_system_function.call_count, 3)

    def test_set_protocol_iter_parses_and_delegates(self):
        cli = _cli_with_orchestrator()
        cli.do_set_protocol_iter('img=3 fluid=7')
        # one execute_system_function per named system
        self.assertEqual(
            cli.orchestrator.execute_system_function.call_count, 2)

    def test_set_protocol_iter_bad_input(self):
        cli = _cli_with_orchestrator()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.do_set_protocol_iter('img=notanint')
        self.assertIn('Input Error', buf.getvalue())


class FluidCommandTest(unittest.TestCase):
    def test_set_valves_delegates(self):
        cli = _cli_with_orchestrator()
        cli.do_set_valves('3')
        cli.orchestrator.execute_system_function.assert_called_once()
        _, kwargs = cli.orchestrator.execute_system_function.call_args
        self.assertEqual(kwargs['kwargs'], {'reservoir_id': 3})

    def test_inject_parses_and_delegates_twice(self):
        cli = _cli_with_orchestrator()
        cli.do_inject('10 velocity=600 pickup_res=2')
        # one call to _set_valves, one to _inject
        self.assertEqual(
            cli.orchestrator.execute_system_function.call_count, 2)

    def test_deliver_delegates(self):
        cli = _cli_with_orchestrator()
        cli.do_deliver(1, 50)
        cli.orchestrator.execute_system_function.assert_called_once()

    def test_fill_tubings_without_fluid_system_prints(self):
        cli = _cli()
        cli.fluid_system = None
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.do_fill_tubings('')
        self.assertIn('needs to be initialized', buf.getvalue())

    def test_fill_tubings_delegates_to_fluid_system(self):
        cli = _cli()
        cli.fluid_system = MagicMock()
        cli.do_fill_tubings('')
        cli.fluid_system.fill_tubings.assert_called_once()


class IlluminationCommandTest(unittest.TestCase):
    def test_laser_delegates_set_and_enable(self):
        cli = _cli_with_orchestrator()
        cli.do_laser('560 1')
        self.assertEqual(
            cli.orchestrator.execute_system_function.call_count, 2)

    def test_power_delegates(self):
        cli = _cli_with_orchestrator()
        cli.do_power('30')
        cli.orchestrator.execute_system_function.assert_called_once()


class LifecycleTest(unittest.TestCase):
    def test_precmd_passthrough(self):
        self.assertEqual(_cli().precmd('hello'), 'hello')

    def test_exit_closes_and_returns_true(self):
        cli = _cli_with_orchestrator()
        cli.orchestrator.poll_protocol_finished.return_value = True
        self.assertTrue(cli.do_exit(''))
        cli.orchestrator.end_orchestration.assert_called_once()

    def test_close_aborts_when_not_finished(self):
        cli = _cli_with_orchestrator()
        cli.orchestrator.poll_protocol_finished.return_value = False
        cli.close()
        cli.orchestrator.abort_orchestration.assert_called_once()

    def test_close_without_orchestrator_is_safe(self):
        _cli().close()  # orchestrator is None -> no-op


class LoadProtocolTest(unittest.TestCase):
    def test_load_protocol_assigns_to_systems(self):
        cli = _cli()
        cli.fluid_system = MagicMock()
        cli.imaging_system = MagicMock()
        fd, path = tempfile.mkstemp(suffix='.yaml')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(
                    "fluid:\n  protocol_entries: []\n"
                    "img:\n  protocol_entries: []\n")
            cli.do_load_protocol(path)
        finally:
            os.unlink(path)
        cli.fluid_system._assign_protocol.assert_called_once()
        cli.imaging_system._assign_protocol.assert_called_once()
        # no 'illu' key -> illumination system cleared
        self.assertIsNone(cli.illumination_system)


if __name__ == '__main__':
    unittest.main()
