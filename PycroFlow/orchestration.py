#!/usr/bin/env python
"""
    PycroFlow/hamilton_upperlevel.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Concertation between Fluid automation, image acquisition, and illumination

------
# Test of fluid-only orchestration:
import PycroFlow.orchestration as por
import PycroFlow.hamilton_architecture as ha

prot = {'fluid': por.protocol['fluid']}
ha.connect('18', 9600)
la = ha.LegacyArchitecture(ha.legacy_system_config, ha.legacy_tubing_config, '18', 9600)
po = por.ProtocolOrchestrator(prot, fluid_system=la)
po.start_orchestration()
po.start_protocol()
po.abort_protocol()
po.abort_orchestration()
------
# Test of imaging-only orchestration:
import PycroFlow.orchestration as por
import PycroFlow.imaging as pi
prot = {'img': por.protocol['img']}
prot['img']['protocol_entries'] = prot['img']['protocol_entries'][1:]  # skip first wait
imaging_config = {'save_dir': r'.', 'base_name': 'test', 'imaging_settings': {'frames': 50, 't_exp': 100}, 'mm_parameters': {'channel_group': 'Filter turret', 'filter': '2-G561',},}

isy = pi.ImagingSystem(imaging_config)
po = por.ProtocolOrchestrator(prot, imaging_system=isy)
# po.imaging_handler.run_protocol()  # non-threaded execution
# or threaded execution
po.start_orchestration()
po.start_protocol()
------
# Test of fluid-and-imaging orchestration:
import PycroFlow.orchestration as por
import PycroFlow.hamilton_architecture as ha
import PycroFlow.imaging as pi

prot = {'img': por.protocol['img'], 'fluid': por.protocol['fluid']}
imaging_config = {'save_dir': r'.', 'base_name': 'test', 'imaging_settings': {'frames': 50, 't_exp': 100}, 'mm_parameters': {'channel_group': 'Filter turret', 'filter': '2-G561',},}

ha.connect('18', 9600)
la = ha.LegacyArchitecture(ha.legacy_system_config, ha.legacy_tubing_config, '18', 9600)
isy = pi.ImagingSystem(imaging_config)
po = por.ProtocolOrchestrator(prot, imaging_system=isy, fluid_system=la)
po.start_orchestration()
po.start_protocol()
#po.abort_protocol()
#po.abort_orchestration()
------

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import threading
import queue
import time
import abc
import logging


logger = logging.getLogger(__name__)


protocol_fluid = [
    {'$type': 'inject', 'reservoir_id': 0, 'volume': 500, 'wait_time': 1},
    {'$type': 'incubate', 'duration': 120},
    {'$type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600, 'wait_time': 1},
    {'target': 'fluid', '$type': 'signal', 'value': 'fluid round 1 done'},
    {'$type': 'flush', 'flushfactor': 1},
    {'$type': 'wait for signal', 'target': 'img', 'value': 'round 1 done'},
    {'$type': 'inject', 'reservoir_id': 14, 'volume': 500, 'wait_time': 1},
]

protocol_imaging = [
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'acquire', 'frames': 100, 't_exp': 100, 'round': 1, 'message': 'R3'},
    {'$type': 'signal', 'value': 'imaging round 1 done'},
]

protocol_illumination = [
    {'$type': 'power', 'value': 1},
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'power', 'value': 50},
    {'$type': 'wait for signal', 'target': 'img', 'value': 'round 1 done'},
]

protocol = {
    'fluid': {
        'parameters': {
            'start_velocity': 50,
            'max_velocity': 1000,
            'stop_velocity': 500,
            'mode': 'tubing_stack',  # or 'tubing_flush'
            'extractionfactor': 1},
        'protocol_entries': protocol_fluid},
    'img': {
        'protocol_entries': protocol_imaging},
    'illu': {
        'protocol_entries': protocol_illumination}
}




class AbstractSystem(abc.ABC):
    def __init__(self):
        pass

    @abc.abstractmethod
    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pass

    @abc.abstractmethod
    def pause_execution(self):
        """Pause protocol execution
        """
        pass

    @abc.abstractmethod
    def resume_execution(self):
        """Resume protocol execution after pausing
        """
        pass

    @abc.abstractmethod
    def abort_execution(self):
        """Abort protocol execution
        """
        pass


class AbstractSystemHandler(threading.Thread, abc.ABC):

    target = ''

    def __init__(self, protocol, threadexchange):
        # super(threading.Thread, self).__init__()
        super().__init__()
        self.protocol = protocol
        logger.debug('starting {:s} system handler with protocol {:s}'.format(self.target, str(self.protocol)))
        self.txchange = threadexchange
        self.system = None  # is set in Handler subclasses

    def run(self):
        if self.system is None:
            self.txchange[self.target + '_finished'].set()
            return
        while ((not self.txchange['abort_flag'].is_set())
               and (not self.txchange['graceful_stop_flag'].is_set())):
            if self.txchange['start_protocol_flag'].is_set():
                logger.debug('starting to run protocol')
                self.run_protocol()
                while not self.poll_protocol_finished():
                    if ((self.txchange['abort_flag'].is_set()
                         or self.txchange['abort_protocol_flag'].is_set())):
                        return
                    time.sleep(.05)
                self.txchange['start_protocol_flag'].clear()

            self.work_queue()
            time.sleep(.1)

    def run_protocol(self):
        logger.debug('start running protocol: {:s}'.format(str(self.protocol['protocol_entries'])))
        nsteps = len(self.protocol['protocol_entries'])
        # potentially, a start protocol entry was given?
        msg = self.search_message('start entry:')
        if msg:
            start_entry = int(msg[len('start entry:'):].strip())
        else:
            start_entry = 0

        for i, step in enumerate(start_entry, self.protocol['protocol_entries']):
            logger.debug('System {:s} performing step {:d}/{:d}: {:s}'.format(self.target, i+1, nsteps, str(step)))
            print('System ', self.target, ' performing step', i+1, '/', nsteps, ':', step)
            if step['$type'].lower() == 'signal':
                self.send_message(step['value'])
            elif step['$type'].lower() == 'wait for signal':
                self.wait_xchange(step['target'], step['value'])
            elif step['$type'].lower() == 'incubate':
                tic = time.time()
                while time.time() < tic + step['duration']:
                    if ((self.txchange['abort_flag'].is_set()
                         or self.txchange['abort_protocol_flag'].is_set())):
                        return
                    time.sleep(.05)
            else:
                self.execute_protocol_entry(i)

            goon_housekeeping = self.housekeeping()
            if not goon_housekeeping:
                self.send_message('Ending.')
                return

        self.txchange[self.target + '_finished'].set()
        return

    def housekeeping(self):
        if ((self.txchange['abort_protocol_flag'].is_set()
             or self.txchange['abort_flag'].is_set())):
            self.system.abort_execution()
            return False
        elif self.txchange['pause_protocol_flag'].is_set():
            self.system.pause_execution()
            return False
        else:
            return True

    def wait_xchange(self, target, message):
        busy = True
        while (busy
               and not self.txchange['abort_flag'].is_set()
               and not self.txchange['abort_protocol_flag'].is_set()
               and not self.txchange['pause_protocol_flag'].is_set()):
            with self.txchange[target + '_lock']:
                if ((message in self.txchange[target]
                     or (target + ' ' + message) in self.txchange[target])):
                    busy = False
                    break
            time.sleep(.05)

    def send_message(self, message):
        with self.txchange[self.target + '_lock']:
            self.txchange[self.target].append(message)

    def search_message(self, substring):
        with self.txchange[self.target + '_lock']:
            messages = self.txchange[self.target]
        for msg in messages:
            if substring in msg:
                return msg
        else:
            return None

    def poll_protocol_finished(self):
        events = [
            v for k, v in self.txchange.items()
            if '_finished' in k]
        finished = [ev.is_set() for ev in events]
        return all(finished)

    @abc.abstractmethod
    def work_queue(self):
        pass


class FluidHandler(AbstractSystemHandler):

    target = 'fluid'

    def __init__(self, fluid_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = fluid_system
        if self.system is not None:
            # assign the protocol - restructure this later on
            self.system._assign_protocol(protocol)

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + '_lock']:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        try:
            item = self.txchange['fluid_queue'].get(timeout=.05)
        except queue.Empty:
            item = None
        if item:
            if item['fun'] == 'deliver':
                self.deliver_fluid(*item['args'], **item('kwargs'))

    def deliver_fluid(self, reservoir_id, volume):
        """Deliver fluid of a given reservoir
        """
        with self.txchange[self.target + '_lock']:
            self.system.deliver_fluid(reservoir_id, volume)


class ImagingHandler(AbstractSystemHandler):

    target = 'img'

    def __init__(self, imaging_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = imaging_system
        if self.system is not None:
            self.system._assign_protocol(protocol)

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + '_lock']:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        pass


class IlluminationHandler(AbstractSystemHandler):

    target = 'illu'

    def __init__(self, illumination_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = illumination_system

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + '_lock']:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        pass


class ProtocolOrchestrator():
    """Takes a protocol and distributes the tasks to the different systems,
    waiting at
    """
    threadexchange = {
        'fluid_lock': threading.Lock(),
        'fluid': [],
        'fluid_finished': threading.Event(),
        'fluid_queue': queue.Queue(),
        'img_lock': threading.Lock(),
        'img': [],
        'img_finished': threading.Event(),
        'illu_lock': threading.Lock(),
        'illu': [],
        'illu_finished': threading.Event(),
        'start_protocol_flag': threading.Event(),
        'pause_protocol_flag': threading.Event(),
        'abort_protocol_flag': threading.Event(),
        'abort_flag': threading.Event(),
        'graceful_stop_flag': threading.Event(),
    }

    def __init__(self, protocol,
                 imaging_system=None, fluid_system=None,
                 illumination_system=None):
        self.fluid_system = fluid_system
        self.fluid_handler = FluidHandler(
            fluid_system, protocol.get('fluid', []),
            self.threadexchange)

        self.imaging_system = imaging_system
        self.imaging_handler = ImagingHandler(
            imaging_system, protocol.get('img', []),
            self.threadexchange)

        self.illumination_system = illumination_system
        self.illumination_handler = IlluminationHandler(
            illumination_system, protocol.get('illu', []),
            self.threadexchange)

        self.protocol = protocol

    def start_orchestration(self):
        self.fluid_handler.start()
        self.imaging_handler.start()
        self.illumination_handler.start()

    def start_protocol(self, system_steps={}):
        """
        Args:
            system steps : dict
                sets the start steps of various systems
                keys: e.g. 'fluid', 'img', 'illu'
                vals: int
        """
        if system_steps != {}:
            for syst, step in system_steps.items():
                with self.threadexchange[syst + '_lock']:
                    self.threadexchange[syst].append(f'start entry: {step}')

        self.threadexchange['start_protocol_flag'].set()

    def abort_protocol(self):
        self.threadexchange['abort_protocol_flag'].set()

    def pause_protocol(self):
        self.threadexchange['pause_protocol_flag'].set()

    def resume_protocol(self):
        self.threadexchange['pause_protocol_flag'].clear()

    def abort_orchestration(self):
        self.threadexchange['abort_flag'].set()
        self.fluid_handler.join()
        self.imaging_handler.join()
        self.illumination_handler.join()

    def poll_protocol_finished(self):
        events = [
            v for k, v in self.threadexchange.items()
            if '_finished' in k]
        finished = [ev.is_set() for ev in events]
        return all(finished)

    def end_orchestration(self):
        self.threadexchange['graceful_stop_flag'].set()
        self.fluid_handler.join()
        self.imaging_handler.join()
        self.illumination_handler.join()

    def enqueue_fluid_function(self, function, args, kwargs):
        self.threadexchange['fluid_queue'].put(
            {'fun': function, 'args': args, 'kwargs': kwargs})

    def execute_system_function(self, target, fun, args=[], kwargs={}):
        """Execute a function of a target system (e.g. Hamilton fluid system).
        This should be done via this function instead of directly because the
        system is also called by another thread and should therefore only be
        accessed within a lock.
        Args:
            target : str
                the target (e.g 'fluid')
            fun : callable
                the function to call (e.g. self.fluid_system._pump)
            args : list
                the arguments to the function
            kwargs : dict
                the keyword arguments to the function
        """
        with self.threadexchange[target + '_lock']:
            fun(*args, **kwargs)

    def __del__(self):
        try:
            self.abort_orchestration()
        except:
            pass
