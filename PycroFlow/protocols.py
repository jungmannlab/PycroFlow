"""
protocols.py

Transforms aggregated protocols (Exchange-PAINT, MERPAINT, ...)
into linearized protocols for the various subsystems (fluidics, imaging,
illumination)

e.g.

fluid_settings = {
    'vol_wash': 500,  # in ul
    'vol_imager_pre': 500,  # in ul
    'vol_imager_post': 100,  # in ul
    'wait_after_pickup': 10,  # wait time to let fluid settle, in s
    'vol_remove_before_wash': 500,  # volume to remove before washing, and add after washing, to improve efficiency
    'reservoir_names': {
        1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
        7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
    'experiment' : {
        'type': 'Exchange',  # options: ['Exchange', 'MERPAINT', 'FlushTest']
        'wash_buffer': 'Buffer B+',
        'imagers': [
            'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2'],
        'initial_imager': 'Rx',  # if string, do an acquisition before fluid 
}
imaging_settings ={
    'frames': 50000,
    't_exp': 100,  # in ms
    'ROI': [512, 512, 512, 512],
}
illumination_settings = {
    'setup': 'Mercury',
    'laser': 560,
    'power': 30,  #mW
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


the result will be e.g.
protocol_fluid = [
    {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
    {'$type': 'incubate', 'duration': 120},
    {'$type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600},
    {'$type': 'signal', 'value': 'fluid round 1 done'},
    {'$type': 'flush', 'flushfactor': 1},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
    {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
]

protocol_imaging = [
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
    {'$type': 'signal', 'value': 'imaging round 1 done'},
]

protocol_illumination = [
    {'$type': 'power', 'value': 1},
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'power', 'value': 50},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
]

"""
# import ic
import logging
import os
import yaml
from datetime import datetime


logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


class ProtocolBuilder:
    def __init__(self):
        self.steps = {'fluid': {}, 'img': {}, 'illu': {}}
        self.reservoir_vols = {}

    def create_protocol(self, config):
        """Create a protocol based on a configuration file.

        Args:
            config : dict
                flow acquisition configuration with keys:
                    save_dir, base_name
                    fluid_settings, imaging_settings, illumination_settings,
                    mm_parameters
        Returns:
            fname = filename of saved protocol
        """
        steps, reservoir_vols = self.create_steps(config)
        protocol = {}
        if 'fluid' in config.keys():
            protocol['fluid'] = {'protocol_entries': steps['fluid']}
            if 'parameters' in config['fluid'].keys():
                protocol['fluid']['parameters'] = config['fluid']['parameters']
        if 'img' in config.keys():
            protocol['img'] = {'protocol_entries': steps['img']}
            if 'parameters' in config['img'].keys():
                protocol['img']['parameters'] = config['img']['parameters']
        if 'illu' in config.keys():
            protocol['illu'] = {'protocol_entries': steps['illu']}
            if 'parameters' in config['illu'].keys():
                protocol['illu']['parameters'] = config['illu']['parameters']

        # save protocol
        fname = config['base_name'] + datetime.now().strftime('_%y%m%d-%H%M') + '.yaml'
        filename = os.path.join(config['save_dir'], fname)

        with open(filename, 'w') as f:
            yaml.dump(
                protocol, f, default_flow_style=True,
                canonical=True, default_style='"')

        return fname, steps

    def create_steps(self, config):
        """Creates the protocol steps one after another

        Args:
            config : dict
                flow acquisition configuration with keys:
                    save_dir, base_name
                    fluid_settings, imaging_settings, illumination_settings,
                    mm_parameters
        Returns:
            steps : list of dict
                the aria steps.
            reservoir_vols : dict
                keys: reservoir names, values: volumes
        """
        self.steps = {'fluid': [], 'img': [], 'illu': []}
        self.reservoir_vols = {
            id: 0 for id in config['fluid']['settings']['reservoir_names']}
        exptype = config['fluid']['settings']['experiment']['type']
        if exptype.lower() == 'exchange':
            steps, reservoir_vols = self.create_steps_exchange(config)
        elif exptype.lower() == 'merpaint':
            steps, reservoir_vols = self.create_steps_MERPAINT(config)
        elif exptype.lower() == 'flushtest':
            steps, reservoir_vols = self.create_steps_flushtest(config)
        else:
            raise KeyError(
                'Experiment type {:s} not implemented.'.format(exptype))
        return steps, reservoir_vols

    def create_steps_exchange(self, config):
        """Creates the protocol steps for an Exchange-PAINT experiment
        Args:
            config : dict
                flow acquisition configuration with keys:
                    save_dir, base_name
                    fluid_settings, imaging_settings, illumination_settings,
                    mm_parameters
        Returns:
            steps : list of dict
                the aria steps.
            reservoir_vols : dict
                keys: reservoir names, values: volumes
            imground_descriptions : list of str
                a description of each imaging round
        """
        experiment = config['fluid']['settings']['experiment']
        reservoirs = config['fluid']['settings']['reservoir_names']
        imager_vol_pre = config['fluid']['settings']['vol_imager_pre']
        imager_vol_post = config['fluid']['settings']['vol_imager_post']
        wait_after_pickup = config['fluid']['settings'].get('wait_after_pickup', 0)
        wash_vol = config['fluid']['settings']['vol_wash']
        wash_vol_pre = config['fluid']['settings']['vol_wash_pre']
        vol_remove_before_wash = config['fluid']['settings'].get('vol_remove_before_wash', 0)

        initial_imager = experiment.get('initial_imager')

        imgsttg = config['img']['settings']
        illusttg = config.get(['illu'], {}).get('settings')

        # check that all mentioned sources acqually exist
        assert experiment['wash_buffer'] in reservoirs.values()
        assert all(
            [name in reservoirs.values() for name in experiment['imagers']])

        washbuf = experiment['wash_buffer']
        res_idcs = {name: nr - 1 for nr, name in reservoirs.items()}
        res_idcs = {name: nr for nr, name in reservoirs.items()}

        if isinstance(initial_imager, str):
            round = 0
            if illusttg:
                self.create_step_setpower(illusttg['laser'], illusttg['power_acq'])
            imager = initial_imager
            self.create_step_acquire(
                imgsttg['frames'], imgsttg['t_exp'],
                message='round_{:d}-{:s}'.format(round, imager))
            self.create_step_signal(
                system='img', message='done imaging round {:d}'.format(round))
            self.create_step_waitfor_signal(
                system='fluid', target='img',
                message='done imaging round {:d}'.format(round))
            if illusttg:
                self.create_step_waitfor_signal(
                    system='illu', target='img',
                    message='done imaging round {:d}'.format(round))
                self.create_step_setpower(illusttg['laser'], illusttg['power_nonacq'])
            self.create_step_pumpout(volume=vol_remove_before_wash, extractionfactor=1)
            self.create_step_inject(
                volume=wash_vol - vol_remove_before_wash,
                reservoir_id=res_idcs[washbuf],
                wait_time=wait_after_pickup)
            self.create_step_inject(
                volume=vol_remove_before_wash,
                reservoir_id=res_idcs[washbuf],
                extractionfactor=0,
                wait_time=wait_after_pickup)
            # check dark frames
            self.create_step_signal(
                system='fluid', message='done dark-round {:d}'.format(round))
            self.create_step_waitfor_signal(
                system='img', target='fluid',
                message='done dark-round {:d}'.format(round))
            if illusttg:
                self.create_step_waitfor_signal(
                    system='illu', target='fluid',
                    message='done dark-round {:d}'.format(round))
                self.create_step_setpower(illusttg['laser'], illusttg['power_acq'])
            self.create_step_acquire(
                imgsttg['darkframes'], imgsttg['t_exp'],
                message='dark-round_{:d}-{:s}'.format(round, imager))
            self.create_step_signal(
                system='img', message='done imaging dark-round {:d}'.format(round))
            self.create_step_waitfor_signal(
                system='fluid', target='img',
                message='done imaging dark-round {:d}'.format(round))
            if illusttg:
                self.create_step_waitfor_signal(
                    system='illu', target='img',
                message='done imaging dark-round {:d}'.format(round))
                self.create_step_setpower(illusttg['laser'], illusttg['power_nonacq'])
        else:
            self.create_step_pumpout(volume=wash_vol_pre)
            self.create_step_inject(volume=wash_vol_pre, reservoir_id=res_idcs[washbuf])
        for round, imager in enumerate(experiment['imagers']):
            if round < len(experiment['imagers']) - 1:
                last_round = False
            else:
                last_round = True
            if isinstance(initial_imager, str):
                round = round + 1
            self.create_step_pumpout(volume=vol_remove_before_wash, extractionfactor=1)  # reduce volume to wash
            self.create_step_inject(  # wash at low volume
                volume=int(imager_vol_pre - vol_remove_before_wash),
                reservoir_id=res_idcs[imager],
                wait_time=wait_after_pickup)
            self.create_step_inject(  # fill up the missing volume
                volume=int(vol_remove_before_wash),
                reservoir_id=res_idcs[imager],
                extractionfactor=0,
                wait_time=wait_after_pickup)
            self.create_step_signal(
                system='fluid', message='done round {:d}'.format(round))
            self.create_step_waitfor_signal(
                system='img', target='fluid',
                message='done round {:d}'.format(round))
            if illusttg:
                self.create_step_waitfor_signal(
                    system='illu', target='fluid',
                message='done round {:d}'.format(round))
                self.create_step_setpower(illusttg['laser'], illusttg['power_acq'])
            self.create_step_acquire(
                imgsttg['frames'], imgsttg['t_exp'],
                message='round_{:d}-{:s}'.format(round, imager))
            self.create_step_signal(
                system='img', message='done imaging round {:d}'.format(round))
            self.create_step_waitfor_signal(
                system='fluid', target='img',
                message='done imaging round {:d}'.format(round))
            if illusttg:
                self.create_step_waitfor_signal(
                    system='illu', target='img',
                    message='done imaging round {:d}'.format(round))
                self.create_step_setpower(illusttg['laser'], illusttg['power_nonacq'])
            self.create_step_inject(
                volume=int(imager_vol_post), reservoir_id=res_idcs[imager],
                wait_time=wait_after_pickup)
            if not last_round:
                self.create_step_pumpout(volume=vol_remove_before_wash, extractionfactor=1)
                self.create_step_inject(
                    volume=wash_vol - vol_remove_before_wash,
                    reservoir_id=res_idcs[washbuf],
                    wait_time=wait_after_pickup)
                self.create_step_inject(
                    volume=vol_remove_before_wash,
                    reservoir_id=res_idcs[washbuf],
                    extractionfactor=0,
                    wait_time=wait_after_pickup)
                # check dark frames
                self.create_step_signal(
                    system='fluid', message='done dark-round {:d}'.format(round))
                self.create_step_waitfor_signal(
                    system='img', target='fluid',
                    message='done dark-round {:d}'.format(round))
                if illusttg:
                    self.create_step_waitfor_signal(
                        system='illu', target='fluid',
                    message='done dark-round {:d}'.format(round))
                    self.create_step_setpower(illusttg['laser'], illusttg['power_acq'])
                self.create_step_acquire(
                    imgsttg['darkframes'], imgsttg['t_exp'],
                    message='dark-round_{:d}-{:s}'.format(round, imager))
                self.create_step_signal(
                    system='img', message='done imaging dark-round {:d}'.format(round))
                self.create_step_waitfor_signal(
                    system='fluid', target='img',
                    message='done imaging dark-round {:d}'.format(round))

        return self.steps, self.reservoir_vols

    def create_steps_MERPAINT(self, config):
        """Creates the protocol steps for an MERPAINT experiment
        Args:
            experiment : dict
                the experiment configuration
                Items:
                    wash_buffer : str
                        the name of the wash buffer reservoir (typically 2xSSC)
                    wash_buffer_vol : float
                        the wash volume in µl
                    hybridization_buffer : str
                        the name of the hybridization buffer reservoir
                    hybridization_buffer_vol : float
                        the hybridization buffer volume in µl
                    hybridization_time : float
                        the hybridization incubation time in s
                    imaging_buffer : str
                        the name of the imaging buffer reservoir (typically C+)
                    imaging_buffer_vol : float
                        the imaging buffer volume in µl
                    imagers : list
                        the names of the imager reservoirs to use (for MERPAINT
                        typically only 1)
                    imager_vol : float
                        the volume to flush imagers in µl
                    adapters : list
                        the names of the secondary adapter reservoirs to use
                    adapter_vol : float
                        the volume to flush adapters in µl
                    erasers : list
                        the names of the eraser reservoirs to unzip
                        the secondary adapters to use
                    eraser_vol : float
                        the volume to flush erasers in µl
                    check_dark_frames : int (optional)
                        if present, add steps to check de-hybridization,
                        and image
                        for said number of frames
            reservoirs : dict
                keys: 1-10, values: the names of the reservoirs
        Returns:
            steps : list of dict
                the aria steps.
            reservoir_vols : dict
                keys: reservoir names, values: volumes
        """
        experiment = config['fluid']['settings']['experiment']
        reservoirs = config['fluid']['settings']['reservoir_names']
        # check that all mentioned sources acqually exist
        assert experiment['wash_buffer'] in reservoirs.values()
        assert experiment['hybridization_buffer'] in reservoirs.values()
        assert experiment['imaging_buffer'] in reservoirs.values()
        assert all(
            [name in reservoirs.values() for name in experiment['imagers']])
        assert all(
            [name in reservoirs.values() for name in experiment['adapters']])
        assert all(
            [name in reservoirs.values() for name in experiment['erasers']])

        imagers = experiment['imagers']

        washbuf = experiment['wash_buffer']
        hybbuf = experiment['hybridization_buffer']
        imgbuf = experiment['imaging_buffer']
        washvol = experiment['wash_buffer_vol']
        hybvol = experiment['hybridization_buffer_vol']
        imgbufvol = experiment['imaging_buffer_vol']
        imagervol = experiment['imager_vol']
        adaptervol = experiment['adapter_vol']
        eraservol = experiment['adapter_vol']
        hybtime = experiment['hybridization_time']

        darkframes = experiment.get('check_dark_frames')
        if darkframes:
            check_dark_frames = True
        else:
            check_dark_frames = False
            darkframes = 0

        imgsttg = config['img']['settings']

        res_idcs = {name: nr for nr, name in reservoirs.items()}

        self.create_step_inject(10, res_idcs[washbuf])
        for merpaintround, (adapter, eraser) in enumerate(zip(
                experiment['adapters'], experiment['erasers'])):
            # hybridization buffer
            self.create_step_inject(hybvol, res_idcs[hybbuf])
            # adapter
            self.create_step_inject(adaptervol, res_idcs[adapter])
            # incubation
            self.create_step_incubate(hybtime)
            # 2xSSC
            self.create_step_inject(washvol, res_idcs[washbuf])
            # iterate over imagers
            for imager_round, imager in enumerate(imagers):
                #   imaging buffer
                self.create_step_inject(imgbufvol, res_idcs[imgbuf])
                #   imager
                self.create_step_inject(imagervol, res_idcs[imager])
                # acquire movie, possibly in multiple rois
                sglmsg = (
                    'done fluids merpaint round {:d}'.format(merpaintround)
                    + ', imager round {:d}'.format(imager_round))
                self.create_step_signal(system='fluid', message=sglmsg)
                self.create_step_waitfor_signal(
                    system='img', target='fluid', message=sglmsg)
                fname = (
                    'merpaintround{:d}'.format(merpaintround)
                    + '-imagerround{:d}'.format(imager_round))
                self.create_step_acquire(
                    imgsttg['frames'], imgsttg['t_exp'], message=fname)
                sglmsg = (
                    'done imaging merpaint round {:d}'.format(merpaintround)
                    + ', imager round {:d}'.format(imager_round))
                self.create_step_signal(system='img', message=sglmsg)
                self.create_step_waitfor_signal(
                    system='fluid', target='img', message=sglmsg)
            # de-hybridize adapter
            # washbuf
            self.create_step_inject(washvol, res_idcs[washbuf])
            # hybridization buffer
            self.create_step_inject(hybvol, res_idcs[hybbuf])
            # eraser
            self.create_step_inject(eraservol, res_idcs[eraser])
            # incubation
            self.create_step_incubate(hybtime)
            # washbuf
            self.create_step_inject(washvol, res_idcs[washbuf])
            if check_dark_frames:
                #   imaging buffer
                self.create_step_inject(imgbufvol, res_idcs[imgbuf])
                #   acquire movie
                sglmsg = 'done darktest fluids round {:d}'.format(round)
                self.create_step_signal(
                    system='fluid', message=sglmsg)
                self.create_step_waitfor_signal(
                    system='img', target='fluid', message=sglmsg)
                fname = (
                    'darktest-merpaintround{:d}'.format(merpaintround)
                    + '-imagerround{:d}'.format(imager_round))
                self.create_step_acquire(
                    imgsttg['darkframes'], imgsttg['t_exp'], message=fname)
                sglmsg = 'done darktest imaging round {:d}'.format(round)
                self.create_step_signal(
                    system='img', message=sglmsg)
                self.create_step_waitfor_signal(
                    system='fluid', target='img', message=sglmsg)
                # washbuf
                self.create_step_inject(washvol, res_idcs[washbuf])

        return self.steps, self.reservoir_vols

    def create_steps_flushtest(self, config):
        """Creates the protocol steps for testing flush volumes:
        Image acquisition is triggered both when washing and when flushing
        imagers
        Args:
            experiment : dict
                the experiment configuration
                Items:
                    fluids : list of str
                        the names of the imager or wash buffer reservoirs
                        to use
                    fluid_vols : list of float
                        the volumes of fluids to flush
            reservoirs : dict
                keys: 1-10, values: the names of the reservoirs
        Returns:
            steps : list of dict
                the aria steps.
            reservoir_vols : dict
                keys: reservoir names, values: volumes
            imground_descriptions : list of str
                a description of each imaging round
        """
        experiment = config['fluid_settings']['experiment']
        reservoirs = config['fluid_settings']['reservoir_names']
        assert all(
            [name in reservoirs.values() for name in experiment['fluids']])

        res_idcs = {name: nr - 1 for nr, name in reservoirs.items()}

        imgsttg = config['imaging_settings']

        for round, (fluid, fluid_vol) in enumerate(
                zip(experiment['fluids'], experiment['fluid_vols'])):
            # flush during acquisition
            self.create_step_inject(int(fluid_vol), res_idcs[fluid])
            fname = (
                'flush-image-round{:d}'.format(round))
            self.create_step_acquire(
                imgsttg['frames'], imgsttg['t_exp'], message=fname)
            # after flushing and acquiring, synchronize again
            sglmsg_i = 'done imaging round {:d}'.format(round)
            sglmsg_f = 'done flushing round {:d}'.format(round)
            self.create_step_signal(
                system='img', message=sglmsg_i)
            self.create_step_signal(
                system='fluid', message=sglmsg_f)
            self.create_step_waitfor_signal(
                system='img', target='fluid', message=sglmsg_f)
            self.create_step_waitfor_signal(
                system='fluid', target='img', message=sglmsg_i)

        return self.steps, self.reservoir_vols

    def create_step_incubate(self, t_incu):
        """Creates a step to wait for a TTL pulse.

        Args:
            steps : dict
                the protocols
            t_incu : float
                the incubation time in seconds
        Returns:
            step : dict
                the step configuration
        """
        timeoutstr = str(t_incu)
        self.steps['fluid'].append(
            {'$type': 'incubate', 'duration': timeoutstr})

    def create_step_pumpout(
            self, volume, extractionfactor=None):
        """Creates a step to pump out only.
        Args:
            volume : int
                volume to pump out in integer µl
            extractionfactor : None or float
                the extractionfactor to use, if different from default
        Returns:
            step : dict
                the step configuration
        """
        volume = volume if volume > 0 else 1
        pars = {
            '$type': 'pump_out',
            'volume': volume}
        if extractionfactor is not None:
            pars['extractionfactor'] = extractionfactor
        self.steps['fluid'].append(pars)

    def create_step_inject(
            self, volume, reservoir_id, wait_time=0, extractionfactor=None):
        """Creates a step to wait for a TTL pulse.
        Args:
            volume : int
                volume to inject in integer µl
            reservoir_id : int
                the reservoir to use
            wait_time : int
                number of seconds to wait between pickup and dispense
            extractionfactor : None or float
                an extraction factor different to the default for this step.
                if None: Default
        Returns:
            step : dict
                the step configuration
        """
        volume = volume if volume > 0 else 1
        self.steps['fluid'].append(
            {'$type': 'inject',
             'volume': volume,
             'reservoir_id': reservoir_id,
             'wait_time': wait_time})
        if extractionfactor is not None:
            self.steps['fluid'][-1]['extractionfactor'] = extractionfactor
        self.reservoir_vols[reservoir_id] += volume

    def create_step_signal(self, system, message):
        self.steps[system].append(
            {'$type': 'signal',
             'value': message})

    def create_step_waitfor_signal(self, system, target, message):
        self.steps[system].append(
            {'$type': 'wait for signal',
             'target': target,
             'value': message})

    def create_step_acquire(self, nframes, t_exp, message):
        self.steps['img'].append(
            {'$type': 'acquire',
             'frames': nframes,
             't_exp': t_exp,
             'message': message})

    def create_step_setpower(self, laser, power):
        self.steps['illu'].append(
            {'$type': 'set power',
             'laser': laser,
             'power': power})
