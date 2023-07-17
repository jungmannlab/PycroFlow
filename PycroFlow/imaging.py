"""
imaging.py

Provides imaging functionality to be used as a system
in orchestration.

imaging config e.g.
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
    'illumination_settings': illumination_settings,
    'mm_parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
}

imaging protocol e.g.
protocol_imaging = [
    {'type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'type': 'acquire', 'frames': 10000, 't_exp': 100, 'message': 'round_1'},
    {'type': 'signal', 'value': 'imaging round 1 done'},
]

"""
import os
import time
import logging
import threading
# import ic
from datetime import datetime
from pycromanager import Acquisition, multi_d_acquisition_events, Core, Studio

from PycroFlow.orchestration import AbstractSystem


logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


class ImagingSystem(AbstractSystem):
    def __init__(self, config, protocol, core=None, studio=None):
        self.config = config
        self.protocol = protocol

        if core is not None:
            self.core = core
        else:
            self.core = Core()
        if studio is not None:
            self.studio = studio
        else:
            self.studio = Studio(convert_camel_case=True)

        self.create_savedir()
        self.create_starttime()

        # test whether all is set up correctly
        self.test_acquisition()

        self.acq_lock = threading.Lock()
        self.acq_pause = threading.Event()
        self.acq_abort = threading.Event()

        logger.debug('Imaging system is set up and ready.')

    def create_savedir(self):
        sdir = os.path.join(
            self.config['save_dir'],
            datetime.now().strftime('%y%m%d') + '_' + self.config['base_name'])
        if not os.path.exists(sdir):
            os.mkdir(sdir)
        self.config['save_dir'] = sdir

    def create_starttime(self):
        self.starttime_str = datetime.now().strftime('_%y-%m-%d_%H%M')

    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pentry = self.protocol[i]
        if pentry['$type'] == 'acquire':
            logger.debug(
                'executing protocol entry {:d}: {:s}'.format(i, str(pentry)))
            acquisition_config = self.config.copy()
            if pentry.get('frames'):
                acquisition_config['frames'] = pentry['frames']
            if pentry.get('t_exp'):
                acquisition_config['t_exp'] = pentry['t_exp']
            acq_name = (
                acquisition_config['base_name']
                + self.starttime_str
                + '_round{:d}_{:s}'.format(
                    round, acquisition_config['message']))
            self.record_movie(acq_name, acquisition_config)

    def pause_execution(self):
        """Pause protocol execution
        """
        self.acq_pause.set()

    def resume_execution(self):
        """Resume protocol execution after pausing
        """
        self.acq_pause.clear()

    def abort_execution(self):
        """Abort protocol execution
        """
        self.acq_abort.set()

    def check_finished(self):
        pass
        # if self.acq_lock:
        #     if self.acq_th.i == self.acq_th.n - 1:
        #         self.acq_th.join()

    def test_acquisition(self):
        # test the possibility to acquire (fail early)
        if self.studio.live().is_live_mode_on():
            self.studio.live().set_live_mode_on(False)
        events = multi_d_acquisition_events(
            num_time_points=10, time_interval_s=.1)
        with Acquisition(
                directory=self.config['save_dir'], name='testacquisition',
                show_display=False, debug=True) as acq:
            acq.acquire(events)

    def record_movie(self, acq_name, acquisition_config):
        """Records a movie via pycromanager
        Args:
            acq_name : str
                the name of the acquisition; pycromanager creates the
                respective dir
            acquisition_config : dict
                the acquisition configuration, comprising following keys:
                    save_dir : the directory to save the acquisition in
                    frames : the number of frames to acquire
                    t_exp : the exposure time.
        """
        acq_dir = acquisition_config['save_dir']
        n_frames = acquisition_config['frames']
        t_exp = acquisition_config['t_exp']
        # chan_group = acquisition_config['mm_parameters']['channel_group']
        # filter = acquisition_config['mm_parameters']['filter']
        # roi = acquisition_config['ROI']

        with Acquisition(directory=acq_dir, name=acq_name, show_display=True,
                         ) as acq:
            events = multi_d_acquisition_events(
                num_time_points=n_frames,
                time_interval_s=0,  # t_exp/1000,
                # channel_group=chan_group, channels=[filter],
                channel_exposures_ms=[t_exp],
                order='tcpz',
            )
            acq.acquire(events)

    def record_movie_in_thread(self, acq_name, acquisition_config):
        """Records a movie via pycromanager
        Args:
            acq_name : str
                the name of the acquisition; pycromanager creates the
                respective dir
            acquisition_config : dict
                the acquisition configuration, comprising following keys:
                    save_dir : the directory to save the acquisition in
                    frames : the number of frames to acquire
                    t_exp : the exposure time.
        """
        acq_dir = acquisition_config['save_dir']
        n_frames = acquisition_config['frames']
        t_exp = acquisition_config['t_exp']
        # chan_group = acquisition_config['mm_parameters']['channel_group']
        # filter = acquisition_config['mm_parameters']['filter']
        # roi = acquisition_config['ROI']

        acq_th = AcquisitionThread(
            self.acq_lock, self.acq_pause, self.acq_abort,
            acq_dir, acq_name, n_frames, t_exp)
        acq_th.run()


class AcquisitionThread(threading.Thread):
    def __init__(self, acq_lock, acq_pause, acq_abort,
                 acq_dir, acq_name, n_frames, t_exp):
        self.lock = acq_abort
        self.ev_pause = acq_pause
        self.ev_abort = acq_abort
        self.acq_dir = acq_dir
        self.acq_name = acq_name
        self.n_frames = n_frames
        self.t_exp = t_exp
        self.i = 0
        self.n = 0

    def run(self):
        with Acquisition(directory=self.acq_dir, name=self.acq_name,
                         show_display=True,
                         pre_hardware_hook_fn=self.hook_fn,
                         ) as acq:
            events = multi_d_acquisition_events(
                num_time_points=self.n_frames,
                time_interval_s=0,  # t_exp/1000,
                # channel_group=chan_group, channels=[filter],
                channel_exposures_ms=[self.t_exp],
                order='tcpz',
            )
            self.n = len(events)
            acq.acquire(events)

    def hook_fn(self):
        self.abort_on_event()
        self.pause_on_event()
        self.i += 1

    def pause_on_event(self):
        while self.ev_pause.is_set():
            time.sleep(.02)
            self.abort_on_event()

    def abort_on_event(self):
        if self.ev_abort.is_set():
            raise Exception('Aborting acquisition due to abort hook')
