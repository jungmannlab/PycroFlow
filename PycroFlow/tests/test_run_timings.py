"""Tests for run-log placement and the measured-vs-estimated step timings.

Two behaviours the run's *record* depends on: the log files follow the
acquisition into its output folder, and every executed step leaves a
machine-parseable timing record there that
:mod:`PycroFlow.protocols.timing_analysis` can mine to improve the estimates.
"""
import json
import os
import tempfile
import unittest
import unittest.mock

import PycroFlow
from PycroFlow.protocols.timing import STEP_TIMING_TAG
from PycroFlow.protocols.timing_analysis import (
    format_summary, parse_step_timings, summarize)


def _timing_line(system, type_, actual, estimate, **extra):
    record = {'system': system, 'type': type_,
              'actual_s': actual, 'estimate_s': estimate}
    record.update(extra)
    return "2026-01-01 | INFO -> {} {}".format(
        STEP_TIMING_TAG, json.dumps(record))


class TestLogRedirect(unittest.TestCase):

    def setUp(self):
        self._cwd = os.getcwd()
        self._configured = PycroFlow.logging_configured()

    def tearDown(self):
        os.chdir(self._cwd)
        # Leave global logging as we found it for the rest of the suite.
        PycroFlow._LOGGING_CONFIGURED = self._configured

    def test_redirect_moves_log_into_acquisition_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            acq = os.path.join(tmp, 'acquisition')
            PycroFlow.setup_logging(
                logfile=os.path.join(tmp, 'pycroflow.log'),
                hamilton_logfile=os.path.join(tmp, 'hamilton.log'))
            target = PycroFlow.redirect_logging(acq)
            self.assertEqual(target, os.path.join(acq, 'pycroflow.log'))
            # The folder is created and both sinks moved.
            from loguru import logger
            logger.info("after redirect")
            logger.complete()
            self.assertTrue(os.path.isfile(target))
            with open(target) as f:
                self.assertIn("after redirect", f.read())

    def test_redirect_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            acq = os.path.join(tmp, 'acq')
            PycroFlow.setup_logging(logfile=os.path.join(tmp, 'p.log'),
                                    hamilton_logfile=None)
            first = PycroFlow.redirect_logging(acq)
            self.assertEqual(PycroFlow.redirect_logging(acq), first)

    def test_redirect_noop_without_setup(self):
        PycroFlow._LOGGING_CONFIGURED = False
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(PycroFlow.redirect_logging(tmp))
            self.assertEqual(os.listdir(tmp), [])

    def test_error_log_captures_warnings_and_tracebacks(self):
        from loguru import logger
        with tempfile.TemporaryDirectory() as tmp:
            errors = os.path.join(tmp, 'errors.log')
            PycroFlow.setup_logging(
                logfile=os.path.join(tmp, 'p.log'), hamilton_logfile=None,
                error_logfile=errors)
            logger.info("routine progress")
            logger.warning("something looks off")
            try:
                raise ValueError("boom")
            except ValueError:
                logger.exception("step failed")
            logger.complete()
            with open(errors) as f:
                text = f.read()
        self.assertNotIn("routine progress", text)   # errors only
        self.assertIn("something looks off", text)
        self.assertIn("ValueError: boom", text)      # with the traceback

    def test_redirect_moves_the_error_log_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            acq = os.path.join(tmp, 'acq')
            PycroFlow.setup_logging(
                logfile=os.path.join(tmp, 'p.log'),
                hamilton_logfile=os.path.join(tmp, 'h.log'),
                error_logfile=os.path.join(tmp, 'errors.log'))
            PycroFlow.redirect_logging(acq)
            from loguru import logger
            logger.error("after the move")
            logger.complete()
            self.assertTrue(os.path.isfile(os.path.join(acq, 'errors.log')))
            with open(os.path.join(acq, 'errors.log')) as f:
                self.assertIn("after the move", f.read())


class TestRunRecord(unittest.TestCase):
    """The design + Run Sequence are saved beside the acquisition."""

    def setUp(self):
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def _service(self, tmp):
        from PycroFlow.services import ExperimentService
        svc = ExperimentService()
        svc._experiment_design = {
            'base_name': 'demo',
            'save_dir': os.path.join(tmp, 'acquisition'),
        }
        svc._protocol = {'fluid': {'protocol_entries': [
            {'$type': 'inject', 'volume': 100}]}}
        return svc

    def test_saves_design_and_run_sequence(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            written = svc.save_run_record()
            self.assertEqual(len(written), 2)
            self.assertTrue(
                any(p.endswith('_design.yaml') for p in written))
            self.assertTrue(
                any(p.endswith('_run_sequence.yaml') for p in written))
            for path in written:
                self.assertTrue(os.path.basename(path).startswith('demo_'))
                with open(path) as f:
                    self.assertTrue(yaml.safe_load(f))
            # Written into the acquisition folder, which is created if needed.
            self.assertEqual(
                {os.path.dirname(p) for p in written},
                {os.path.abspath(os.path.join(tmp, 'acquisition'))})

    def test_second_run_does_not_overwrite_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            first = svc.save_run_record()
            with unittest.mock.patch(
                    'PycroFlow.services.experiment_service.datetime') as dt:
                dt.now.return_value.strftime.return_value = '990101-000000'
                second = svc.save_run_record()
            self.assertNotEqual(set(first), set(second))
            self.assertEqual(
                len(os.listdir(os.path.join(tmp, 'acquisition'))), 4)

    def test_save_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            # A file where the acquisition folder should be -> makedirs fails.
            open(os.path.join(tmp, 'acquisition'), 'w').close()
            self.assertEqual(svc.save_run_record(), [])

    def test_no_design_writes_nothing_anywhere(self):
        # A bare protocol has no acquisition folder; the record must not be
        # scattered into whatever directory the app happens to run from.
        from PycroFlow.services import ExperimentService
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            svc = ExperimentService()
            svc._protocol = {'fluid': {'protocol_entries': []}}
            self.assertEqual(svc.save_run_record(), [])
            self.assertEqual(os.listdir(tmp), [])


class TestTimingAnalysis(unittest.TestCase):

    def test_parses_records_and_ignores_other_traffic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'run.log')
            with open(path, 'w') as f:
                f.write("some unrelated log line\n")
                f.write(_timing_line('fluid', 'inject', 41.2, 33.0,
                                     volume=500, velocity=1800) + "\n")
                f.write("{} not-json\n".format(STEP_TIMING_TAG))
                f.write(_timing_line('img', 'acquire', 10.0, 9.0) + "\n")
            records = parse_step_timings(path)
        self.assertEqual([r['type'] for r in records], ['inject', 'acquire'])
        self.assertEqual(records[0]['volume'], 500)
        self.assertEqual(records[0]['source'], path)

    def test_scans_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'a.log'), 'w') as f:
                f.write(_timing_line('fluid', 'inject', 1.0, 1.0) + "\n")
            with open(os.path.join(tmp, 'b.log'), 'w') as f:
                f.write(_timing_line('img', 'acquire', 2.0, 2.0) + "\n")
            self.assertEqual(len(parse_step_timings(tmp)), 2)

    def test_summary_ratio_flags_underestimates(self):
        records = [
            {'system': 'fluid', 'type': 'inject',
             'actual_s': 40.0, 'estimate_s': 20.0},
            {'system': 'fluid', 'type': 'inject',
             'actual_s': 60.0, 'estimate_s': 30.0},
            {'system': 'img', 'type': 'wait for signal',
             'actual_s': 5.0, 'estimate_s': 0.0},
        ]
        summary = summarize(records)
        inject = summary[('fluid', 'inject')]
        self.assertEqual(inject['n'], 2)
        self.assertEqual(inject['actual_median'], 50.0)
        self.assertEqual(inject['ratio'], 2.0)   # takes twice the estimate
        # Nothing estimated -> no ratio rather than a division blow-up.
        self.assertIsNone(summary[('img', 'wait for signal')]['ratio'])
        text = format_summary(summary)
        self.assertIn('inject', text)
        self.assertIn('2.00', text)

    def test_summary_of_no_records_is_empty(self):
        self.assertEqual(summarize([]), {})


class TestStepTimingEmission(unittest.TestCase):

    def test_handler_logs_measured_and_estimated_duration(self):
        from PycroFlow.orchestration.core import AbstractSystemHandler

        captured = []

        class Handler(AbstractSystemHandler):
            target = 'fluid'

            def __init__(self):   # bypass the threading machinery
                self.protocol = {'parameters': {'max_velocity': 1000}}

            def execute_protocol_entry(self, i):
                pass

            def work_queue(self):
                pass

        from loguru import logger
        sink = logger.add(lambda msg: captured.append(str(msg)), level='INFO')
        try:
            Handler()._log_step_timing(
                {'$type': 'inject', 'volume': 500, 'velocity': 1000},
                index=2, nsteps=7, elapsed=42.5)
        finally:
            logger.remove(sink)

        line = [c for c in captured if STEP_TIMING_TAG in c]
        self.assertEqual(len(line), 1)
        record = json.loads(line[0].split(STEP_TIMING_TAG, 1)[1].strip())
        self.assertEqual(record['system'], 'fluid')
        self.assertEqual(record['step'], 3)       # 1-based for humans
        self.assertEqual(record['type'], 'inject')
        self.assertEqual(record['actual_s'], 42.5)
        self.assertEqual(record['estimate_s'], 60.0)   # 120 * 500 / 1000
        self.assertEqual(record['volume'], 500)

    def test_timing_failure_never_breaks_a_run(self):
        from PycroFlow.orchestration.core import AbstractSystemHandler

        class Handler(AbstractSystemHandler):
            target = 'fluid'

            def __init__(self):
                self.protocol = None   # -> .get() raises inside the logger

            def execute_protocol_entry(self, i):
                pass

            def work_queue(self):
                pass

        # Must swallow the error rather than take the protocol thread down.
        Handler()._log_step_timing({'$type': 'inject'}, 0, 1, 1.0)


if __name__ == '__main__':
    unittest.main()
