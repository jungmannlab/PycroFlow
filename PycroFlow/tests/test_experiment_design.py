"""Tests for the high-level experiment design: schema, setup configs,
builder split, and the ExperimentService translate path."""
import os
import tempfile
import unittest

import PycroFlow
from PycroFlow import configs
from PycroFlow.protocols import ProtocolBuilder
from PycroFlow.schemas import (
    validate_experiment_design,
    ExperimentDesignValidationError,
)
from PycroFlow.services import ExperimentService


_EXAMPLE = os.path.join(
    os.path.dirname(PycroFlow.__file__), 'examples', 'sph_resi_6plex.yaml')


def _example_design():
    svc = ExperimentService()
    return svc.load_experiment_design(_EXAMPLE)


class TestExperimentDesignSchema(unittest.TestCase):

    def test_accepts_example_sphresi(self):
        design = _example_design()
        model = validate_experiment_design(design)
        self.assertEqual(model.fluid.settings.experiment.type, 'SPH-RESI')

    def test_accepts_exchange(self):
        design = {
            'base_name': 'x',
            'fluid': {'settings': {
                'vol_wash': 10, 'vol_imager_post': 5,
                'reservoir_names': {1: 'R1'},
                'experiment': {'type': 'Exchange', 'wash_buffer': 'B',
                               'imagers': ['R1']}}},
            'img': {'settings': {'t_exp': 100, 'frames': 100}},
        }
        model = validate_experiment_design(design)
        self.assertEqual(model.fluid.settings.experiment.type, 'Exchange')

    def test_rejects_bad_experiment_type(self):
        design = _example_design()
        design['fluid']['settings']['experiment']['type'] = 'NOPE'
        with self.assertRaises(ExperimentDesignValidationError):
            validate_experiment_design(design)

    def test_rejects_missing_required(self):
        design = _example_design()
        del design['fluid']['settings']['vol_wash']
        with self.assertRaises(ExperimentDesignValidationError):
            validate_experiment_design(design)

    def test_hyphenated_aliases_round_trip(self):
        model = validate_experiment_design(_example_design())
        d = model.model_dump(by_alias=True)
        exp = d['fluid']['settings']['experiment']
        self.assertIn('target-rounds', exp)
        tr = exp['target-rounds']['A1']
        self.assertIn('RESI-rounds', tr)
        self.assertIn('RESI-imager', tr)

    def test_power_nonacq_defaults_to_acq(self):
        model = validate_experiment_design({
            'base_name': 'x',
            'fluid': {'settings': {
                'vol_wash': 1, 'reservoir_names': {1: 'R1'},
                'experiment': {'type': 'Exchange', 'wash_buffer': 'B',
                               'imagers': ['R1']}}},
            'img': {'settings': {'t_exp': 100}},
            'illu': {'settings': {'laser': 642, 'power_acq': 70}},
        })
        self.assertEqual(model.illu.settings.power_nonacq, 70)


class TestSetupConfigs(unittest.TestCase):

    def test_list_setups(self):
        setups = configs.list_setups()
        self.assertIn('Emulator', setups)
        self.assertIn('Mercury', setups)

    def test_load_setup_tubing_tuple_keys(self):
        setup = configs.load_setup('Mercury')
        self.assertFalse(setup['emulated'])
        # tubing records convert to a tuple-keyed dict
        self.assertIn(('pump_a', 'sample'), setup['tubing'])

    def test_assemble_filters_and_attaches(self):
        setup = configs.load_setup('Emulator')
        ham, tub = configs.assemble_hamilton_config(setup, {
            'reservoir_names': {1: 'A1', 7: 'C+'},
            'special_names': {'flushbuffer_a': 7, 'h2o': 16},
            'cleaning_reservoirs': ['h2o'],
        })
        ids = sorted(r['id'] for r in ham['reservoir_a'])
        self.assertEqual(ids, [1, 7, 16])   # 16 pulled in via cleaning 'h2o'
        self.assertEqual(ham['special_names']['flushbuffer_a'], 7)
        self.assertEqual(ham['cleaning_reservoirs'], ['h2o'])
        self.assertIn('interface', ham)

    def test_assemble_unknown_reservoir_raises(self):
        setup = configs.load_setup('Emulator')
        with self.assertRaises(KeyError):
            configs.assemble_hamilton_config(
                setup, {'reservoir_names': {999: 'nope'}})


class TestBuilderSplit(unittest.TestCase):

    def test_build_protocol_no_io(self):
        design = _example_design()
        before = set(os.listdir('.'))
        protocol = ProtocolBuilder().build_protocol(design)
        after = set(os.listdir('.'))
        self.assertEqual(before, after)          # nothing written
        self.assertIn('fluid', protocol)
        self.assertGreater(len(protocol['fluid']['protocol_entries']), 0)

    def test_create_protocol_writes(self):
        design = _example_design()
        with tempfile.TemporaryDirectory() as d:
            design['save_dir'] = d
            fname, steps = ProtocolBuilder().create_protocol(design)
            self.assertTrue(os.path.exists(os.path.join(d, fname)))
            self.assertIn('fluid', steps)


class TestEmulatedFluidOps(unittest.TestCase):

    def _connected(self):
        from PycroFlow.services import SystemService
        svc = SystemService()
        svc.load_setup('Emulator')
        svc.connect_fluid({
            'parameters': {'max_velocity': 200, 'clean_velocity': 200,
                           'clean_delay': 0},
            'settings': {'reservoir_names': {1: 'R1', 7: 'C+'},
                         'special_names': {'flushbuffer_a': 7, 'h2o': 16},
                         'cleaning_reservoirs': ['h2o']},
        })
        return svc

    def test_fill_tubings_runs(self):
        svc = self._connected()
        svc.fill_tubings()   # over the fake serial; must not raise

    def test_clean_tubings_is_gui_safe(self):
        from unittest import mock
        svc = self._connected()
        # No terminal prompt: even with input() sabotaged, clean runs.
        with mock.patch(
                'builtins.input',
                side_effect=AssertionError("input() must not be called")):
            svc.clean_tubings()


class TestTranslate(unittest.TestCase):

    def test_translate_from_design(self):
        svc = ExperimentService()
        svc.load_experiment_design(_EXAMPLE)
        protocol = svc.translate()
        self.assertEqual(svc.state.value, 'loaded')
        self.assertGreater(len(protocol['fluid']['protocol_entries']), 0)
        self.assertGreater(len(protocol['img']['protocol_entries']), 0)
        self.assertGreater(len(protocol['illu']['protocol_entries']), 0)

    def test_attach_systems_feeds_translate(self):
        from unittest.mock import MagicMock
        svc = ExperimentService()
        fluid = MagicMock(name='fluid')
        svc.attach_systems(fluid_system=fluid)
        svc.load_experiment_design(_EXAMPLE)
        svc.translate()
        self.assertIs(svc.orchestrator.fluid_system, fluid)


if __name__ == '__main__':
    unittest.main()
