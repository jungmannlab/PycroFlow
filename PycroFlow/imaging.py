"""
imaging.py

Provides imaging functionality to be used as a system
in orchestration.

imaging config e.g.
flow_acq_config = {
    'save_dir': r'Z://users//grabmayr//microscopy_data',
    'base_name': 'AutomationTest_R2R4',
    'imaging_settings': {
        'frames': 50000,
        't_exp': 100,  # in ms
        'ROI': [512, 512, 512, 512],},
    'mm_parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
}

imaging protocol e.g.
protocol_imaging = [
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'acquire', 'frames': 10000, 't_exp': 100, 'message': 'round_1'},
    {'$type': 'signal', 'value': 'imaging round 1 done'},
]

"""
import os
import time
import logging
import threading
# import ic
from datetime import datetime, timedelta
from pycromanager import Acquisition, multi_d_acquisition_events, Core, Studio
import pandas as pd

from PycroFlow.orchestration import AbstractSystem
from PycroFlow.util import ProgressBar


logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


class ImagingSystem(AbstractSystem):
    def __init__(self, config, core=None, studio=None):
        self.config = config

        if core is not None:
            self.core = core
        else:
            self.core = Core()
        if studio is not None:
            self.studio = studio
        else:
            self.studio = Studio(convert_camel_case=True)

        # PFS logging
        self.pfs_pars = {  # for Mercury
            'tag_pfs': 'TIPFSOffset',
            'tag_zdrive': 'TIZDrive',
            'tag_status': 'TIPFSStatus',
            'prop_state': 'State',
            'prop_status': 'Status',
            'deltat': 10}
        self.pfs_log = pd.DataFrame({
            'datetime': [datetime.now()],
            'frame': [0],
            'pfs': [self.core.get_position(self.pfs_pars['tag_pfs'])],
            'zdrive': [self.core.get_position(self.pfs_pars['tag_zdrive'])],
            'status': [self.core.get_property(
                self.pfs_pars['tag_status'],
                self.pfs_pars['prop_status'])],
            'state': [self.core.get_property(
                self.pfs_pars['tag_status'],
                self.pfs_pars['prop_state'])]
        })

        self.create_savedir()
        self.create_starttime()

        # test whether all is set up correctly
        self.test_acquisition()

        self.acq_lock = threading.Lock()
        self.acq_pause = threading.Event()
        self.acq_abort = threading.Event()

        logger.debug('Imaging system is set up and ready.')

    def create_savedir(self):
        # sdir = os.path.join(
        #     self.config['save_dir'],
        #     datetime.now().strftime('%y%m%d') + '_' + self.config['base_name'])
        sdir = os.path.join(
            self.config['save_dir'], self.config['base_name'])
        if not os.path.exists(sdir):
            os.mkdir(sdir)
        else:
            ndirs = [it for it in os.listdir() if sdir in it]
            sdir += '_{:d}'.format(ndirs + 1)
            os.mkdir(sdir)

        self.config['save_dir'] = sdir

    def create_starttime(self):
        self.starttime_str = datetime.now().strftime('_%y-%m-%d_%H%M')

    def _assign_protocol(self, protocol):
        self.protocol = protocol

    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pentry = self.protocol['protocol_entries'][i]
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
                + '_prtclstep{:d}_{:s}'.format(
                    i, pentry['message']))
            self.record_movie(acq_name, acquisition_config)
            logger.debug('done executing protocol entry {:d}'.format(i))

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
                show_display=False, debug=False) as acq:
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
        # chan_group = self.protocol['parameters']['channel_group']
        # filter = self.protocol['parameters']['filter']
        # roi = self.protocol['parameters']['ROI']

        # record PFS locations
        self.pfs_log = pd.DataFrame(
            columns=['datetime', 'frame', 'pfs', 'state', 'status', 'zdrive'],
            index=range(int(acquisition_config['frames']/100)))
        self.curr_frame = 0

        if self.protocol['parameters'].get('show_progress'):
            self.probar = ProgressBar('Acquisition', n_frames)
        with Acquisition(directory=acq_dir, name=acq_name, show_display=self.protocol['parameters'].get('show_display', True),
                         image_process_fn=self.image_process_fn) as acq:
            events = multi_d_acquisition_events(
                num_time_points=n_frames,
                time_interval_s=0,  # t_exp/1000,
                # channel_group=chan_group, channels=[filter],
                channel_exposures_ms=[t_exp],
                order='tcpz',
            )
            acq.acquire(events)
            if self.protocol['parameters'].get('show_display', True):
                try:
                    viewer = acq.get_viewer()
                except:
                    viewer = None
                    pass
        time.sleep(.2)
        if viewer is not None and self.protocol['parameters'].get('close_display_after_acquisition', True):
            viewer.close()
        self.pfs_log.to_excel(os.path.join(acq_dir, acq_name + '_pfs.xlsx'))
        if self.protocol['parameters'].get('show_progress'):
            self.probar.end_progress()
        logger.debug('acquired all images of {:s}'.format(acq_name))

    def image_process_fn(self, img, meta):
        if self.protocol['parameters'].get('show_progress'):
            try:
                self.probar.progress_increment()
            except Exception as e:
                print(e)
        # log PFS position
        if self.curr_frame % 100 == 0:
            self.pfs_log.loc[int(self.curr_frame/100)] = {
                'datetime': datetime.now(),
                'frame': self.curr_frame,
                'pfs': self.core.get_position(self.pfs_pars['tag_pfs']),
                'zdrive': self.core.get_position(self.pfs_pars['tag_zdrive']),
                'state': self.core.get_property(
                    self.pfs_pars['tag_status'], self.pfs_pars['prop_state']),
                'status': self.core.get_property(
                    self.pfs_pars['tag_status'], self.pfs_pars['prop_status']),
            }
        self.curr_frame += 1

        return (img, meta)

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
