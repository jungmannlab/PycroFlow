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


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


mm_app_path = r'C:\Program Files\Micro-Manager-2.0'
config_file = r'C:\Users\miblab\Desktop\MMConfig_1.cfg'

save_dir = r"Z:\users\grabmayr\FlowAutomation\testdata"
base_name = 'exchange_experiment'
n_rounds = 4
n_frames = 10
t_exp = .1
aria_timeout = 30*60
# laser = 561
# laser_power = 35

from pathlib import Path
from time import sleep
from functools import partial
import gc

# this function is executed once the acquisition engine has setup
def post_hook_fn(event,bridge,event_queue):

    # open bridge to MM
    bridge = Bridge()
    core = bridge.get_core()

    return event

# this function makes sure that all data is written to disk
def storage_monitor_callback_fn(final_z,final_c,final_e,axes):

    global data_storage_finished

    print(axes['z'],axes['c'],axes['e'])

    if axes['z']==(final_z-1) and axes['c']==(final_c-1) and axes['e']==(final_e-1):
        print('Setting flag, writing last image.')
        data_storage_finished = True

def test_acq3():

    from pycromanager import __version__
    print('pycromanager', __version__)

    from zmq import __version__
    print('zmq', __version__)

    core = Core()

    events = multi_d_acquisition_events(num_time_points=n_frames)

    # Loop over N acquisitions
    for i in range(4):
        print (f'\nAcquisition {i}')
        time.sleep(10)

        try:
            with Acquisition(directory=save_dir, name='test', show_display=False, debug=False) as acq:
                acq.acquire(events)

                del acq       # Adding ths didn't help
                gc.collect()  # Adding ths didn't help
                print('\tAll good!')

        except Exception as e:
            print(e)


def test_acq2():
    start_headless(mm_app_path, config_file, timeout=5000)

    print('started headless')

    acq_name = r'test_acquisition'

    # global data_storage_finished
    # # construct partial functions for acquisition hooks
    # storage_monitor_callback_metadata_fn = partial(storage_monitor_callback_fn, n_frames, 1, 1)
    #
    # data_storage_finished = False

    with Acquisition(
            directory=save_dir, name=acq_name, show_display=False,
                     ) as acq:
        print('creating events')
        events = multi_d_acquisition_events(num_time_points=n_frames)
        print('printing events')
        for e in events:
            print(e)
        print('acquiring')
        acq.acquire(events)

    # # wait until all data is written to disk
    # while not(data_storage_finished):
    #     sleep(1)
    #     print('Storage not finished')

    # clean up this acquisition object and force Python to run garbage collector
    acq = None
    gc.collect()


    print('second run')

    acq_name = r'test_acquisition2'

    with Acquisition(directory=save_dir, name=acq_name, show_display=False,
                     ) as acq:
        print('creating events')
        events = multi_d_acquisition_events(num_time_points=n_frames)
        print('printing events')
        for e in events:
            print(e)
        print('acquiring')
        acq.acquire(events)


def test_acq():
    start_headless(mm_app_path, config_file, timeout=5000)

    print('started headless')

    acq_name = r'test_acquisition'

    with Acquisition(directory=save_dir, name=acq_name, show_display=False,
                     ) as acq:
        print('creating events')
        events = multi_d_acquisition_events(
            num_time_points=n_frames,
            time_interval_s=t_exp,
            channel_group='Filter turret', channels=['2-G561'],
            channel_exposures_ms= [t_exp],
        )
        print('printing events')
        for e in events:
            print(e)
        print('acquiring')
        acq.acquire(events)

def main():
    # Start the Java process
    # start_headless(mm_app_path, config_file, timeout=5000)
    core = Core()

    print('started headless')

    # start aria triggering connection
    aria = AriaTrigger()
    print('initialized triggering')

    for round in range(n_rounds):
        acq_name = base_name + '_{:d}'.format(round)

        print('waiting for Aria pulsing to signal readiness for round {:d}'.format(round))
        aria.sense_pulse(timeout=aria_timeout)
        print('received pulse, now starting acquisition.')
        record_movie(save_dir, acq_name, n_frames, t_exp)

        print('Acquisition of ', acq_name, 'done.')
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
            channel_group='Filter turret', channels=['2-G561'],
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
