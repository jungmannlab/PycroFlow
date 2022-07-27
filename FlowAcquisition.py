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

from pycromanager import Acquisition, multi_d_acquisition_events, start_headless
# import monet.control as mcont
from FlowAcquisition import AriaTrigger


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


mm_app_path = '/path/to/micromanager'
config_file = mm_app_path + "/MMConfig_demo.cfg"

save_dir = r"C:\Users\henry\Desktop\data"
base_name = 'exchange_experiment'
n_rounds = 4
n_frames = 30000
t_exp = .1
# laser = 561
# laser_power = 35


def main():
    # Start the Java process
    start_headless(mm_app_path, config_file, timeout=5000)

    # start aria triggering connection
    aria = AriaTrigger()

    for round in n_rounds:
        acq_name = base_name + '_{:d}'.format(round)

        aria.sense_pulse()
        record_movie(save_dir, acq_name, n_frames, t_exp)

        print('acquisition of ', acq_name, 'done.')
        aria.send_pulse()


def image_saved_fn(axes, dataset):
    # pixels = dataset.read_image(**axes)
    # TODO: on-the-fly testing and quality control of data
    pass

def record_movie(acq_dir, acq_name, n_frames, t_exp):
    with Acquisition(directory=acq_dir, name=acq_name, show_display=False,
                    image_saved_fn=image_saved_fn,
                     ) as acq:
        events = multi_d_acquisition_events(
            num_time_points=n_frames,
            time_interval_s=t_exp,
            channel_group='Channel', channels=['Cy3B'],
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
    main()
