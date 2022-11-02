#!/usr/bin/env python
"""
    PycroFlow/FlowAcquisition.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A first script to start on the topic of automating Exchange-PAINT
    experiments using Pycromanager and Fluigent Aria.

    Usage:
    * Start this program first (python FlowAcquisition.py in pycroflow environemnt)
    * Start Aria
    * follow instructions in command line

    Aria Protocol for simple exchange experiment:
    * 10ul Buffer injection, ending in TTL
    * Wait for external TTL
        (here, a pause can be made, for connecting the slide)
    * iteratively, for all rounds:
        - inject 200ul respective imager, ending with TTL
        - Wait for external TTL (here, the acquisition takes place)
        - inject 1000ul buffer

    Aria Protocol for MERPAINT experiment:
    * 10ul Buffer injection, ending in TTL
    * Wait for external TTL
        (here, a pause can be made, for connecting the slide)
    * same as above, but with interleaved hybridization of multiplex-adapter

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
import sys
from logging import handlers
from icecream import ic
from inputimeout import inputimeout, TimeoutOccurred

from pycromanager import Acquisition, multi_d_acquisition_events, start_headless, Core, Bridge
# import monet.control as mcont
from arduino_connection import AriaTrigger
from AriaComm import AriaConnection
from AriaProtocol import create_protocol as _create_protocol
import time
from datetime import datetime
from time import sleep


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


mm_app_path = r'C:\Program Files\Micro-Manager-2.0'
config_file = r'C:\Users\miblab\Desktop\MMConfig_1.cfg'
#
# save_dir = r"Z:\users\grabmayr\FlowAutomation\testdata"
# base_name = 'exchange_experiment'
# n_rounds = 4
# n_frames = 50000
# t_exp = .1
# max_duration_aria = 30*60  # in s
# aria_TTL_duration = 0.3  # in s
# laser = 561
# laser_power = 35

flow_acq_config = {
    'rounds': 2,
    'frames': 500,
    't_exp': 100,  # in ms
    'save_dir': r"Z:\users\grabmayr\FlowAutomation\testdata",
    'base_name': 'aria_exchange_experiment',
    'aria_parameters': {
        'use_TTL': False,
        'max_flowstep': 30*60,  # in s
        'TTL_duration': 0.3,  # in s
        'vol_wash': 1000,  # in ul
        'vol_imager': 200,  # in ul
        'reservoir_names': {
            1: 'R 1', 2: 'R 2', 3: 'R 3', 4: 'R 4', 5: 'R 5', 6: 'R 6',
            7: 'Res7', 8: 'Res8', 9: 'Res9', 10: 'Buffer B+'},
        'experiment' : {
            'type': 'Exchange',  # options: ['Exchange', 'MERPAINT']
            'wash_buffer': 'Buffer B+',
            'imagers': ['R 2', 'R 4'],
        },
        # 'protocol_folder': r'../testdata/'
        'protocol_folder': r'C:\Users\miblab\AppData\Local\Fluigent\Aria\Sequences'
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


def optional_break(timeout=5):
    try:
        ipt = inputimeout(
            'Proceed? [Y/N - default Y, respond within {:.1f}s]'.format(timeout),
            timeout=timeout)
    except TimeoutOccurred:
        ipt = 'Y'
    if 'N' in ipt.upper():
        # user input
        ipt = input('Enter anything when ready.')
    print('proceeding.')


def main(acquisition_config, dry_run=False, break_for_slide=True):
    """
    Args:
        acquisition_config : dict
            the configuration
        dry_run : bool
            do not use aria if True
        break_for_slide : bool
            do a nonoptional break for slide connection
    """
    starttime_str = datetime.now().strftime('_%y-%m-%d_%H%M')

    # Start the Java process
    # start_headless(mm_app_path, config_file, timeout=5000)
    core = Core()

    print('Connected to Micromanager.')

    # start aria triggering connection
    if not dry_run:
        protocol_file = ''
        protocol_file = _create_protocol(
            acquisition_config['aria_parameters'],
            acquisition_config['base_name'])
        if acquisition_config['aria_parameters']['use_TTL']:
            aria = AriaTrigger(acquisition_config['aria_parameters'])
        else:
            aria = AriaConnection()
        print('initialized triggering.')
        print('Please set Aria TTL duration to 300 ms, load Aria protocol {:s} and start it now.'.format(
            protocol_file))

    # first item in aria protocol must be a minute buffer injection, ending
    # with a trigger signal, and followed by a "wait for TTL" step
    if not dry_run:
        print('Waiting for aria to have pre-injected the buffers.')
        aria.sense_trigger()
        print('Ready to connect and mount slide.')
        if break_for_slide:
            input('Press Enter to continue')
        else:
            optional_break()
        aria.send_trigger()

    for round in range(acquisition_config['rounds']):
        acq_name = (acquisition_config['base_name'] +
                    starttime_str + '_round{:d}'.format(round))

        if not dry_run:
            print('waiting for Aria pulsing to signal readiness for round {:d}'.format(round))
            aria.sense_trigger()
            print('received trigger')
        print('About to start acquisition {:s}.'.format(acq_name))
        if round==0:
            input('Check focus. Stop Live View when done. Press Enter to continue.')
        else:
            optional_break(timeout=5)

        record_movie(acq_name, acquisition_config)

        print('Acquisition of ', acq_name, 'done.')
        if not dry_run:
            aria.send_trigger()
    print('Finished. Now cleaning will take 1-2 hours!')


def image_saved_fn(axes, dataset):
    # pixels = dataset.read_image(**axes)
    # TODO: on-the-fly testing and quality control of data
    pass

def start_progress(title):
    global progress_x
    sys.stdout.write(title + ": [" + "-"*40 + "]" + chr(8)*41)
    sys.stdout.flush()
    progress_x = 0

def progress(x):
    global progress_x
    x = int(x * 40 // 100)
    sys.stdout.write("#" * (x - progress_x))
    sys.stdout.flush()
    progress_x = x

def end_progress():
    sys.stdout.write("#" * (40 - progress_x) + "]\n")
    sys.stdout.flush()

def image_process_fn(img, meta):
    global nimgs_acquired, nimgs_total
    nimgs_acquired+=1
    progress(nimgs_acquired/nimgs_total)


def test_record_movie():
    acq_name = flow_acq_config['base_name']
    acquisition_config = flow_acq_config
    record_movie(acq_name, acquisition_config)

def record_movie(acq_name, acquisition_config):
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

    global nimgs_acquired, nimgs_total
    nimgs_acquired = 0
    nimgs_total = n_frames
    start_progress('Acquisition')

    with Acquisition(directory=acq_dir, name=acq_name, show_display=True,
                     image_process_fn=image_process_fn,
                     ) as acq:
        events = multi_d_acquisition_events(
            num_time_points=n_frames,
            # time_interval_s=t_exp/1000,
            # channel_group=chan_group, channels=[filter],
            # channel_exposures_ms= [t_exp],
        )
        #for e in events:
        #    ic(e)
        acq.acquire(events)

    end_progress()


def acq():
    # start_headless(mm_app_path, config_file, timeout=5000)

    # bridge = Bridge()
    # core = bridge.get_core()
    core=Core()
    # mm = bridge.get_studio()
    # pm = mm.positions()
    save_dir = r"Z:\users\grabmayr\FlowAutomation\testdata"
    acq_name = r'exchange_experiment'
    n_frames = 2
    t_exp = 2
    chan_group = 'Filter turret'
    filter = '2-G561'

    with Acquisition(directory=save_dir, name=acq_name, show_display=False, debug=True,
                     ) as acq:
        events = multi_d_acquisition_events(
            num_time_points=n_frames,
        )
        acq.acquire(events)


def config_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'pycroflow.log', maxBytes=1e6, backupCount=5)
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
