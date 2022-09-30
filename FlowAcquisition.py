#!/usr/bin/env python
"""
    PycroFlow/FlowAcquisition.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A first script to start on the topic of automating Exchange-PAINT
    experiments using Pycromanager and Fluigent Aria.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic

from pycromanager import Acquisition, multi_d_acquisition_events, start_headless, Core
# import monet.control as mcont
from arduino_connection import AriaTrigger
import time
from time import sleep


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


# mm_app_path = r'C:\Program Files\Micro-Manager-2.0'
# config_file = r'C:\Users\miblab\Desktop\MMConfig_1.cfg'
#
# save_dir = r"Z:\users\grabmayr\FlowAutomation\testdata"
# base_name = 'exchange_experiment'
# n_rounds = 4
# n_frames = 100
# t_exp = .1
# max_duration_aria = 30*60  # in s
# aria_TTL_duration = 0.3  # in s
# laser = 561
# laser_power = 35

flow_acq_config = {
    'rounds': 4,
    'frames': 100,
    't_exp': .1,  # in s
    'save_dir': r"Z:\users\grabmayr\FlowAutomation\testdata",
    'base_name': 'exchange_experiment'
    'aria_parameters': {
        'max_flowstep': 30*60,  # in s
        'TTL_duration': 0.3,  # in s
    },
    'mm_parameters': {
        'mm_app_path': r'C:\Program Files\Micro-Manager-2.0',
        'mm_config_file': r'C:\Users\miblab\Desktop\MMConfig_1.cfg',
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
    'illu_parameters': {
        'laser': 560,
        'power': 35,  #mW
    }
}


def main(acquisition_config):
    # Start the Java process
    # start_headless(mm_app_path, config_file, timeout=5000)
    core = Core()

    print('started headless')

    # start aria triggering connection
    aria = AriaTrigger(acquisition_config['aria_parameters'])
    print('initialized triggering')

    for round in range(acquisition_config['rounds']):
        acq_name = acquisition_config['base_name'] + '_{:d}'.format(round)

        print('waiting for Aria pulsing to signal readiness for round {:d}'.format(round))
        aria.sense_pulse()
        print('received pulse, now starting acquisition.')
        record_movie(acquisition_config)

        print('Acquisition of ', acq_name, 'done.')
        aria.send_pulse()


def image_saved_fn(axes, dataset):
    # pixels = dataset.read_image(**axes)
    # TODO: on-the-fly testing and quality control of data
    pass


def record_movie(acq_name, acquisition_config):
    """Records a movie via pycromanager
    Args:
        acq_name : str
            the name of the acquisition; pycromanager creats the respective dir
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

    with Acquisition(directory=acq_dir, name=acq_name, show_display=False,
                    image_saved_fn=image_saved_fn,
                     ) as acq:
        events = multi_d_acquisition_events(
            num_time_points=n_frames,
            time_interval_s=t_exp,
            channel_group=chan_group, channels=[filter],
            channel_exposures_ms= [t_exp],
        )
        for e in events:
            ic(e)
        acq.acquire(events)


def config_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'monet.log', maxBytes=1e6, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    # logger.addHandler(stream_handler)


if __name__ == "__main__":
    config_logger()
    logger = logging.getLogger(__name__)
    logger.debug('start logging')
    main(flow_acq_config)
