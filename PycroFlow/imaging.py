"""
imaging.py

Provides imaging functionality to be used as a system
in orchestration.

imaging protocol e.g.
protocol_imaging = [
    {'type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
    {'type': 'signal', 'value': 'imaging round 1 done'},
]
"""
from orchestration import AbstractSystem


class ImagingSystem(AbstractSystem):
    def __init__(self):
        pass

    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pass

    def pause_execution(self):
        """Pause protocol execution
        """
        pass

    def resume_execution(self):
        """Resume protocol execution after pausing
        """
        pass

    def abort_execution(self):
        """Abort protocol execution
        """
        pass

    def record_movie(self, acq_name, acquisition_config, core=None):
        """Records a movie via pycromanager
        Args:
            acq_name : str
                the name of the acquisition; pycromanager creates the respective dir
            acquisition_config : dict
                the acquisition configuration, comprising following keys:
                    save_dir : the directory to save the acquisition in
                    frames : the number of frames to acquire
                    t_exp : the exposure time.
        """
        acq_dir = acquisition_config['save_dir']
        n_frames = acquisition_config['frames']
        t_exp = acquisition_config['t_exp']
        chan_group = acquisition_config['mm_parameters']['channel_group']
        filter = acquisition_config['mm_parameters']['filter']
        roi = acquisition_config['ROI']

    #    if core is not None:
    #       core.set_exposure(t_exp)
    #       core.set_config(chan_group, filter)
    #       core.set_roi(*roi)

        start_progress('Acquisition', n_frames)

        with Acquisition(directory=acq_dir, name=acq_name, show_display=True,
                         image_process_fn=image_process_fn,
                         ) as acq:
            events = multi_d_acquisition_events(
                num_time_points=n_frames,
                time_interval_s=0,#t_exp/1000,
                #channel_group=chan_group, channels=[filter],
                channel_exposures_ms= [t_exp],
                order='tcpz',
            )
            #for e in events:
            #    ic(e)
            acq.acquire(events)

        end_progress()
