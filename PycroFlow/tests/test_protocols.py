import unittest
import logging

import PycroFlow.protocols as pprot


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

        steps_expect = {
            'fluid': [{
                '$type': 'inject',
                'volume': volume,
                'reservoir_id': reservoir_id}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

        res_vol_expect = {reservoir_id: volume}

        self.assertEqual(pb.reservoir_vols, res_vol_expect)

    def test_06(self):
        pb = pprot.ProtocolBuilder()

        t_incu = 120
        timeoutstr = str(t_incu)
        pb.create_step_incubate(t_incu)

        steps_expect = {
            'fluid': [{'$type': 'incubate', 'duration': timeoutstr}],
            'illu': [],
            'img': []
        }
        self.assertEqual(pb.steps, steps_expect)

    def test_07(self):
        pb = pprot.ProtocolBuilder()

        fluid_settings = {
            'vol_wash': 500,  # in ul
            'vol_imager_pre': 500,  # in ul
            'vol_imager_post': 100,  # in ul
            'reservoir_names': {
                1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
                7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
            'experiment': {
                'type': 'Exchange',
                'wash_buffer': 'Buffer B+',
                'imagers': [
                    'R4', 'R2']}
        }
        imaging_settings = {
            'frames': 50000,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }

        flow_acq_config = {
            'save_dir': r'Z://users//grabmayr//microscopy_data',
            'base_name': 'AutomationTest_R2R4',
            'fluid_settings': fluid_settings,
            'imaging_settings': imaging_settings,
        }
        pb.reservoir_vols = {
            id: 0 for id in fluid_settings['reservoir_names'].keys()}

        pb.create_steps_exchange(flow_acq_config)

        logger.debug('fluid steps')
        for step in pb.steps['fluid']:
            logger.debug(str(step))
        logger.debug('img steps')
        for step in pb.steps['img']:
            logger.debug(str(step))

        steps_expect = {
            'img': [
                {'$type': 'wait for signal',
                 'target': 'fluid', 'value': 'done round 0'},
                {'$type': 'acquire',
                 'frames': 50000, 't_exp': 100, 'message': 'round_0'},
                {'$type': 'signal', 'value': 'done imaging round 0'},
                {'$type': 'wait for signal',
                 'target': 'fluid', 'value': 'done round 1'},
                {'$type': 'acquire',
                 'frames': 50000, 't_exp': 100, 'message': 'round_1'},
                {'$type': 'signal', 'value': 'done imaging round 1'}],
            'illu': [],
            'fluid': [
                {'$type': 'inject', 'volume': 10, 'reservoir_id': 9},
                {'$type': 'inject', 'volume': 500, 'reservoir_id': 7},
                {'$type': 'signal', 'value': 'done round 0'},
                {'$type': 'wait for signal',
                 'target': 'img', 'value': 'done imaging round 0'},
                {'$type': 'inject', 'volume': 100, 'reservoir_id': 7},
                {'$type': 'inject', 'volume': 500, 'reservoir_id': 9},
                {'$type': 'inject', 'volume': 500, 'reservoir_id': 6},
                {'$type': 'signal', 'value': 'done round 1'},
                {'$type': 'wait for signal',
                 'target': 'img', 'value': 'done imaging round 1'},
                {'$type': 'inject', 'volume': 100, 'reservoir_id': 6}]
        }

        self.assertEqual(pb.steps, steps_expect)

        logger.debug('reservoir vols: ' + str(pb.reservoir_vols))

        reservoir_vols_expect = {
            1: 0, 3: 0, 5: 0, 6: 600, 7: 600, 8: 0, 9: 510, 10: 0}

        self.assertEqual(pb.reservoir_vols, reservoir_vols_expect)

    def test_08(self):
        pb = pprot.ProtocolBuilder()

        fluid_settings = {
            'vol_wash': 500,  # in ul
            'vol_imager_pre': 500,  # in ul
            'vol_imager_post': 100,  # in ul
            'reservoir_names': {
                1: 'ad_1', 2: 'ad_2', 3: 'ad_3',
                4: 'er_1', 5: 'er_2', 6: 'er_3',
                7: 'R2', 8: 'R4', 9: 'Res9',
                10: 'Buffer B+', 11: 'HybBuf'},
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
                'erasers': ['er_1', 'er_2', 'er_3']}
        }
        imaging_settings = {
            'frames': 50000,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }

        flow_acq_config = {
            'save_dir': r'Z://users//grabmayr//microscopy_data',
            'base_name': 'AutomationTest_R2R4',
            'fluid_settings': fluid_settings,
            'imaging_settings': imaging_settings,
        }
        pb.reservoir_vols = {
            id: 0 for id in fluid_settings['reservoir_names'].keys()}

        pb.create_steps_MERPAINT(flow_acq_config)

        logger.debug('*' * 20 + '    testing MERPAINT')
        logger.debug('fluid steps')
        for step in pb.steps['fluid']:
            logger.debug(str(step))
        logger.debug('img steps')
        for step in pb.steps['img']:
            logger.debug(str(step))

    def test_09(self):
        pb = pprot.ProtocolBuilder()

        fluid_settings = {
            'vol_wash': 500,  # in ul
            'vol_imager_pre': 500,  # in ul
            'vol_imager_post': 100,  # in ul
            'reservoir_names': {
                1: 'ad_1', 2: 'ad_2', 3: 'ad_3',
                4: 'er_1', 5: 'er_2', 6: 'er_3',
                7: 'R2', 8: 'R4', 9: 'Res9',
                10: 'Buffer B+', 11: 'HybBuf'},
            'experiment': {
                'type': 'FlushTest',
                'fluids': ['R4', 'Buffer B+', 'R2'],
                'fluid_vols': [100, 300, 200]}
        }
        imaging_settings = {
            'frames': 50000,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }

        flow_acq_config = {
            'save_dir': r'Z://users//grabmayr//microscopy_data',
            'base_name': 'AutomationTest_R2R4',
            'fluid_settings': fluid_settings,
            'imaging_settings': imaging_settings,
        }
        pb.reservoir_vols = {
            id: 0 for id in fluid_settings['reservoir_names'].keys()}

        pb.create_steps_flushtest(flow_acq_config)

        logger.debug('*' * 20 + '    testing FlushTest')
        logger.debug('fluid steps')
        for step in pb.steps['fluid']:
            logger.debug(str(step))
        logger.debug('img steps')
        for step in pb.steps['img']:
            logger.debug(str(step))

    def test_10(self):
        pb = pprot.ProtocolBuilder()

        fluid_settings = {
            'vol_wash': 500,  # in ul
            'vol_imager_pre': 500,  # in ul
            'vol_imager_post': 100,  # in ul
            'reservoir_names': {
                1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
                7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
            'experiment': {
                'type': 'Exchange',
                'wash_buffer': 'Buffer B+',
                'imagers': [
                    'R4', 'R2']}
        }
        imaging_settings = {
            'frames': 50000,
            't_exp': 100,  # in ms
            'ROI': [512, 512, 512, 512],
        }

        flow_acq_config = {
            'save_dir': r'PycroFlow//TestData',
            'protocol_folder': r'PycroFlow//TestData',
            'base_name': 'AutomationTest_R2R4',
            'fluid_settings': fluid_settings,
            'imaging_settings': imaging_settings,
        }
        pb.reservoir_vols = {
            id: 0 for id in fluid_settings['reservoir_names'].keys()}

        pb.create_protocol(flow_acq_config)
