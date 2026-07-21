import unittest
import logging

import PycroFlow.protocols as pprot
from PycroFlow.tests import TEST_OUTPUT_DIR


logger = logging.getLogger(__name__)


class TestProtocolBuilder(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01(self):
        pb = pprot.ProtocolBuilder()
        del pb

    def test_02(self):
        pb = pprot.ProtocolBuilder()

        frames = 1000
        t_exp = 100
        message = 'msg'
        pb.create_step_acquire(nframes=frames, t_exp=t_exp, message=message)

        steps_expect = {
            'fluid': [],
            'illu': [],
            'img': [{
                '$type': 'acquire',
                'frames': frames,
                't_exp': t_exp,
                'message': message}]
        }
        self.assertEqual(pb.steps, steps_expect)

    def test_03(self):
        pb = pprot.ProtocolBuilder()

        system = 'fluid'
        target = 'img'
        message = 'msg'
        pb.create_step_waitfor_signal(system, target, message)

        steps_expect = {
            'fluid': [{
                '$type': 'wait for signal',
                'target': target,
                'value': message}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

    def test_04(self):
        pb = pprot.ProtocolBuilder()

        system = 'fluid'
        message = 'msg'
        pb.create_step_signal(system, message)

        steps_expect = {
            'fluid': [{
                '$type': 'signal',
                'value': message}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

    def test_05(self):
        pb = pprot.ProtocolBuilder()

        volume = 500
        reservoir_id = 3
        pb.reservoir_vols = {3: 0}
        pb.create_step_inject(volume, reservoir_id)

        # create_step_inject defaults delay=0 and includes it in the entry.
        steps_expect = {
            'fluid': [{
                '$type': 'inject',
                'volume': volume,
                'reservoir_id': reservoir_id,
                'delay': 0}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

        res_vol_expect = {reservoir_id: volume}

        self.assertEqual(pb.reservoir_vols, res_vol_expect)

    def test_06(self):
        pb = pprot.ProtocolBuilder()

        t_incu = 120  # minutes
        pb.create_step_incubate(t_incu)

        # create_step_incubate converts minutes -> seconds and stores the
        # numeric value; orchestration.run_protocol coerces with float().
        steps_expect = {
            'fluid': [{'$type': 'incubate', 'duration': t_incu * 60}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

    def test_07(self):
        # Exchange step builder smoke-test. The exact step list is pinned
        # byte-by-byte in test_regression_protocols.test_create_steps_snapshots
        # (fixture: exchange_basic). Here we just exercise the new nested
        # config schema and assert each subsystem produced non-empty steps.
        pb = pprot.ProtocolBuilder()

        reservoir_names = {
            1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
            7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'}
        flow_acq_config = {
            'save_dir': TEST_OUTPUT_DIR,
            'base_name': 'AutomationTest_R2R4',
            'fluid': {
                'parameters': {},
                'settings': {
                    'vol_wash_pre': 50,
                    'vol_wash': 500,
                    'vol_imager_pre': 500,
                    'vol_imager_post': 100,
                    'vol_remove_before_wash': 50,
                    'wait_after_pickup': 5,
                    'reservoir_names': reservoir_names,
                    'experiment': {
                        'type': 'Exchange',
                        'wash_buffer': 'Buffer B+',
                        'imagers': ['R4', 'R2'],
                    },
                },
            },
            'img': {
                'parameters': {},
                'settings': {
                    'frames': 50000,
                    'darkframes': 50,
                    't_exp': 100,
                },
            },
        }
        pb.reservoir_vols = {id: 0 for id in reservoir_names}

        pb.create_steps_exchange(flow_acq_config)

        self.assertGreater(len(pb.steps['fluid']), 0)
        self.assertGreater(len(pb.steps['img']), 0)

    def test_08(self):
        # MERPAINT step builder smoke-test.
        pb = pprot.ProtocolBuilder()

        reservoir_names = {
            1: 'ad_1', 2: 'ad_2', 3: 'ad_3',
            4: 'er_1', 5: 'er_2', 6: 'er_3',
            7: 'R2', 8: 'R4', 9: 'Res9',
            10: 'Buffer B+', 11: 'HybBuf'}
        flow_acq_config = {
            'save_dir': TEST_OUTPUT_DIR,
            'base_name': 'AutomationTest_R2R4',
            'fluid': {
                'parameters': {},
                'settings': {
                    'vol_wash_pre': 50,
                    'vol_wash': 500,
                    'vol_imager_pre': 500,
                    'vol_imager_post': 100,
                    'vol_remove_before_wash': 50,
                    'wait_after_pickup': 5,
                    'reservoir_names': reservoir_names,
                    'experiment': {
                        'type': 'MERPAINT',
                        'wash_buffer': 'Buffer B+',
                        'hybridization_buffer': 'HybBuf',
                        'imaging_buffer': 'Buffer B+',
                        'wash_buffer_vol': 500,
                        'hybridization_buffer_vol': 750,
                        'imaging_buffer_vol': 400,
                        'imager_vol': 400,
                        'adapter_vol': 400,
                        'hybridization_time': 600,
                        'imagers': ['R4', 'R2'],
                        'adapters': ['ad_1', 'ad_2', 'ad_3'],
                        'erasers': ['er_1', 'er_2', 'er_3'],
                    },
                },
            },
            'img': {
                'parameters': {},
                'settings': {
                    'frames': 50000,
                    'darkframes': 50,
                    't_exp': 100,
                },
            },
        }
        pb.reservoir_vols = {id: 0 for id in reservoir_names}

        pb.create_steps_MERPAINT(flow_acq_config)

        self.assertGreater(len(pb.steps['fluid']), 0)
        self.assertGreater(len(pb.steps['img']), 0)

    def test_09(self):
        # FlushTest step builder smoke-test.
        # NOTE: create_steps_flushtest still reads the *legacy flat* config
        # shape (config['fluid_settings'] / config['imaging_settings']),
        # unlike create_steps_exchange / _MERPAINT which were migrated to the
        # nested 'fluid'/'img' schema. Testing it with the shape it actually
        # consumes; harmonizing flushtest to the nested schema is a separate
        # production change (rig-risk) outside this test fix.
        pb = pprot.ProtocolBuilder()

        reservoir_names = {
            1: 'ad_1', 2: 'ad_2', 3: 'ad_3',
            4: 'er_1', 5: 'er_2', 6: 'er_3',
            7: 'R2', 8: 'R4', 9: 'Res9',
            10: 'Buffer B+', 11: 'HybBuf'}
        flow_acq_config = {
            'save_dir': TEST_OUTPUT_DIR,
            'base_name': 'AutomationTest_R2R4',
            'fluid_settings': {
                'vol_wash': 500,
                'vol_imager_pre': 500,
                'vol_imager_post': 100,
                'reservoir_names': reservoir_names,
                'experiment': {
                    'type': 'FlushTest',
                    'fluids': ['R4', 'Buffer B+', 'R2'],
                    'fluid_vols': [100, 300, 200],
                },
            },
            'imaging_settings': {
                'frames': 50000,
                't_exp': 100,
            },
        }
        pb.reservoir_vols = {id: 0 for id in reservoir_names}

        pb.create_steps_flushtest(flow_acq_config)

        self.assertGreater(len(pb.steps['fluid']), 0)
        self.assertGreater(len(pb.steps['img']), 0)

    def test_10(self):
        # Full create_protocol pipeline test — produces a YAML output file.
        pb = pprot.ProtocolBuilder()

        reservoir_names = {
            1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
            7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'}
        flow_acq_config = {
            'save_dir': TEST_OUTPUT_DIR,
            'protocol_folder': TEST_OUTPUT_DIR,
            'base_name': 'AutomationTest_R2R4',
            'fluid': {
                'parameters': {},
                'settings': {
                    'vol_wash_pre': 50,
                    'vol_wash': 500,
                    'vol_imager_pre': 500,
                    'vol_imager_post': 100,
                    'vol_remove_before_wash': 50,
                    'wait_after_pickup': 5,
                    'reservoir_names': reservoir_names,
                    'experiment': {
                        'type': 'Exchange',
                        'wash_buffer': 'Buffer B+',
                        'imagers': ['R4', 'R2'],
                    },
                },
            },
            'img': {
                'parameters': {},
                'settings': {
                    'frames': 50000,
                    'darkframes': 50,
                    't_exp': 100,
                },
            },
        }
        pb.reservoir_vols = {id: 0 for id in reservoir_names}

        fname, steps = pb.create_protocol(flow_acq_config)
        self.assertTrue(fname.endswith('.yaml'))
        self.assertGreater(len(steps['fluid']), 0)
        self.assertGreater(len(steps['img']), 0)


class TestOptionalDarkframes(unittest.TestCase):
    """Leaving ``darkframes`` empty drops the dark-frame acquisitions."""

    @staticmethod
    def _protocol(darkframes='omit'):
        from PycroFlow.schemas import validate_experiment_design

        design = {
            'base_name': 'x', 'save_dir': TEST_OUTPUT_DIR,
            'fluid': {'settings': {
                'vol_wash': 200., 'vol_reagent': 100., 'vol_imager_post': 50.,
                'reservoir_names': {2: 'Buffer', 3: 'I1', 4: 'I2'},
                'special_names': {},
                'experiment': {'type': 'Exchange', 'wash_buffer': 'Buffer',
                               'imagers': ['I1', 'I2']}}},
            'img': {'settings': {'t_exp': 100., 'frames': 10}},
            'illu': {'settings': {'laser': 560, 'power_acq': 35.}},
        }
        if darkframes != 'omit':
            design['img']['settings']['darkframes'] = darkframes
        design = validate_experiment_design(design).model_dump(by_alias=True)
        return pprot.ProtocolBuilder().build_protocol(design)

    @staticmethod
    def _entries(protocol):
        for sub in protocol.values():
            if isinstance(sub, dict):
                for entry in sub.get('protocol_entries', []):
                    yield entry

    def _acquire_names(self, protocol):
        return [e.get('name') for e in self._entries(protocol)
                if e['$type'] == 'acquire']

    def _fluid_entries(self, protocol):
        return [e['$type'] for e in protocol['fluid']['protocol_entries']]

    def test_darkframes_set_keeps_the_acquisitions(self):
        names = self._acquire_names(self._protocol(darkframes=5))
        self.assertIn('I1 (dark frames)', names)

    def test_omitted_darkframes_drops_the_acquisitions(self):
        protocol = self._protocol()          # field absent entirely
        names = self._acquire_names(protocol)
        self.assertEqual(names, ['I1', 'I2'])
        self.assertFalse([n for n in names if 'dark' in str(n)])
        # No acquire entry may carry a None frame count (which previously
        # failed protocol validation with a confusing pydantic error).
        for entry in self._entries(protocol):
            if entry['$type'] == 'acquire':
                self.assertIsInstance(entry['frames'], int)

    def test_none_and_zero_behave_like_omitted(self):
        for value in (None, 0):
            names = self._acquire_names(self._protocol(darkframes=value))
            self.assertEqual(names, ['I1', 'I2'], msg=repr(value))

    def test_the_wash_is_still_performed(self):
        # Only the acquisition goes away — the wash it followed must stay,
        # or rounds would run into each other.
        with_dark = self._fluid_entries(self._protocol(darkframes=5))
        without = self._fluid_entries(self._protocol())
        self.assertEqual(with_dark.count('inject'), without.count('inject'))
        self.assertEqual(
            with_dark.count('pump_out'), without.count('pump_out'))

    def test_no_orphaned_waits_without_darkframes(self):
        # Dropping steps must not leave a 'wait for signal' whose signal was
        # removed with them — that would deadlock the run.
        protocol = self._protocol()
        signals = {e['value'] for e in self._entries(protocol)
                   if e['$type'] == 'signal'}
        waits = {e['value'] for e in self._entries(protocol)
                 if e['$type'] == 'wait for signal'}
        self.assertEqual(waits - signals, set())
