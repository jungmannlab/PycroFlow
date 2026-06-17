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
import os
from logging import handlers
from icecream import ic
from inputimeout import inputimeout, TimeoutOccurred

from pycromanager import Acquisition, multi_d_acquisition_events, start_headless, Core, Studio
# import monet.control as mcont
from arduino_connection import AriaTrigger
from AriaComm import AriaConnection
from AriaProtocol import create_protocol as _create_protocol
import time
from datetime import datetime
from time import sleep
import yaml

sys.path.insert(0, 'Z:\\users\\grabmayr\\power_calibration\\monet')
from PycroFlow.monet import CONFIGS
from PycroFlow.monet.control import IlluminationLaserControl as ILC



logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


mm_app_path = r'C:\Program Files\Micro-Manager-2.0'
config_file = r'C:\Users\miblab\Desktop\MMConfig_1.cfg'
#
# save_dir = r"Z:\users\grabmayr\FlowAutomation\testdata"
# base_name = 'exchange_experiment'
# n_rounds = 2
# n_frames = 50000
# t_exp = .1
# max_duration_aria = 30*60  # in s
# aria_TTL_duration = 0.3  # in s
# laser = 561
# laser_power = 35

flow_acq_config = {
    'rounds': 10,
    'frames': 50000,
    't_exp': 100,  # in ms
    'ROI': [512, 512, 512, 512],
    'save_dir': r'Z:\users\grabmayr\microscopy_data',#r"Z:\users\grabmayr\FlowAutomation\testdata",
    'base_name': 'AutomationTest_R2R4',
    'aria_parameters': {
        'use_TTL': False,
        'max_flowstep': 30*60,  # in s
        'TTL_duration': 0.3,  # in s
        'vol_wash': 500,  # in ul
        'vol_imager': 500,  # in ul
        'reservoir_names': {
            1: 'R1', 2: 'empt', 3: 'R3', 4: 'empt', 5: 'R5', 6: 'R6',
            7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
        'experiment' : {
            'type': 'Exchange',  # options: ['Exchange', 'MERPAINT', 'FlushTest']
            'wash_buffer': 'Buffer B+',
            'imagers': ['R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2'],
#            'imagers': ['500 pM P 3', '500 pM DB', '1 nM DB'],
#            'imagers': ['R 2', 'R 4'],
#            'imagers': ['R 4'],
        },
        # 'protocol_folder': r'Z:\users\grabmayr\microscopy_data'
        'protocol_folder': r'C:\Users\miblab\AppData\Local\Fluigent\Aria\Sequences'
    },
    'mm_parameters': {
        'mm_app_path': r'C:\Program Files\Micro-Manager-2.0',
        'mm_config_file': r'C:\Users\miblab\Desktop\MMConfig_1.cfg',
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
    'illu_parameters': {
        'setup': 'Mercury',
        'laser': 560,
        'power': 30,  #mW
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

    sdir = os.path.join(
        acquisition_config['save_dir'],
        datetime.now().strftime('%y%m%d')+'_'+acquisition_config['base_name'])
    if not os.path.exists(sdir):
        os.mkdir(sdir)
    acquisition_config['save_dir'] = sdir
    with open(os.path.join(sdir, 'acquisition_configuration.yaml'), 'w') as f:
        yaml.dump(acquisition_config, f)

    # start power control
    if 'illu_parameters' in acquisition_config.keys():
        illuconfig = CONFIGS[acquisition_config['illu_parameters']['setup']]
        laserlaunch = ILC(illuconfig)
        laserlaunch.laser = acquisition_config['illu_parameters']['laser']  # nm
        laserlaunch.power = 1  # mW

    # Start the Java process
    # start_headless(mm_app_path, config_file, timeout=5000)
    core = Core()
    studio = Studio(convert_camel_case=True)

    print('Connected to Micromanager.')

    # test the possibility to acquire (fail early)
    if studio.live().is_live_mode_on():
        studio.live().set_live_mode_on(False)
    events = multi_d_acquisition_events(
        num_time_points=10,time_interval_s=.1)
    with Acquisition(
        directory=acquisition_config['save_dir'], name='testacquisition',
        show_display=False, debug=True) as acq:
        acq.acquire(events)

    # start aria triggering connection
    if not dry_run:
        protocol_file = ''
        protocol_file, imground_descriptions = _create_protocol(
            acquisition_config['aria_parameters'],
            acquisition_config['base_name'])
        if acquisition_config['aria_parameters']['use_TTL']:
            aria = AriaTrigger(acquisition_config['aria_parameters'])
            print('Please set Aria TTL duration to 300 ms, load Aria protocol {:s} and start it now.'.format(
                protocol_file))
        else:
            aria = AriaConnection()
            print('Please load Aria protocol {:s} and start it now.'.format(
                protocol_file))
            aria.wait_for_aria_conn()
        print('initialized triggering.')

    # first item in aria protocol must be a minute buffer injection, ending
    # with a trigger signal, and followed by a "wait for TTL" step
    if not dry_run:
        print('Waiting for aria to have pre-injected the buffers.')
        aria.sense_trigger()
        tic = time.time()
        print('Ready to connect and mount slide.')
        if break_for_slide:
            input('Press Enter to continue')
        else:
            optional_break()
        twait = max([0, tic+15-time.time()])
        time.sleep(twait)
        aria.send_trigger()

    for round, desc in enumerate(imground_descriptions):
        acq_name = (acquisition_config['base_name'] +
                    starttime_str + '_round{:d}_{:s}'.format(round, desc))

        if not dry_run:
            print('waiting for Aria pulsing to signal readiness for round {:d}'.format(round))
            aria.sense_trigger()
            print('received trigger')
        print('About to start acquisition {:s}.'.format(acq_name))
        if round==0 and break_for_slide:
            if 'illu_parameters' in acquisition_config.keys():
                laserlaunch.power = 20 # mW
            input('Check focus. Stop Live View when done. Press Enter to continue.')
        if round==0 and not break_for_slide:
            if 'illu_parameters' in acquisition_config.keys():
                laserlaunch.power = 20 # mW
            print('Now you could check focus. Stop Live View when done.')
            optional_break(timeout=5)
        else:
            optional_break(timeout=5)

        if 'illu_parameters' in acquisition_config.keys():
            laserlaunch.power = acquisition_config['illu_parameters']['power'] # mW

        if studio.live().is_live_mode_on():
            studio.live().set_live_mode_on(False)
        record_movie(acq_name, acquisition_config, core)

        if 'illu_parameters' in acquisition_config.keys():
            laserlaunch.power = 1 # mW

        print('Acquisition of ', acq_name, 'done.')
        if not dry_run:
            aria.send_trigger()
    print('Finished. Now cleaning will take 1-2 hours!')
    if 'illu_parameters' in acquisition_config.keys():
        laserlaunch.power = 1 # mW
        laserlaunch.laser_enabled = False



def image_saved_fn(axes, dataset):
    # pixels = dataset.read_image(**axes)
    # TODO: on-the-fly testing and quality control of data
    pass

def start_progress(ltitle, n_frames):
    global progress_x, title
    global nimgs_acquired, nimgs_total
    nimgs_acquired = 0
    title = ltitle
    nimgs_total = n_frames
    #sys.stdout.write(title + ": [" + "-"*40 + "]" + chr(8)*41)
    #sys.stdout.flush()
    print(title + ": [" + "-"*40 + "]", end='\r')
    progress_x = 0

def progress2(x):
    global progress_x, title
    x = int(x * 40 // 100)
    deci = int((x - int(x))*10)
    sys.stdout.write("#" * (x - progress_x))
    #sys.stdout.write("#" * (x - progress_x-1) + str(deci))
    sys.stdout.flush()
    progress_x = x

def progress(x):
    global title
    deci = int((x - int(x))*10)
    x = int(x * 40 // 100)
    y = max([0, 40-x-1])
    print(title + ": [" + '#'*x + str(deci) +"-"*y + "]", end='\r')
    #print(x, y, deci, x+y+1)


def end_progress():
    #sys.stdout.write("#" * (40 - progress_x) + "]\n")
    #sys.stdout.flush()
    print(title + ": [" + "#"*40 + "]", end='\n')

def image_process_fn(img, meta):
    try:
        global nimgs_acquired, nimgs_total
        nimgs_acquired+=1
        progress(nimgs_acquired/nimgs_total*100)
    except Exception as e:
        print(e)
    return (img, meta)


def test_record_movie():
    acq_name = flow_acq_config['base_name']
    acquisition_config = flow_acq_config
    record_movie(acq_name, acquisition_config)

def record_movie(acq_name, acquisition_config, core=None):
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
    main(flow_acq_config, break_for_slide=True)
