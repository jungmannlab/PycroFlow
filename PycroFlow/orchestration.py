#!/usr/bin/env python
"""
    PycroFlow/hamilton_upperlevel.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Concertation between Fluid automation, image acquisition, and illumination

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import threading
import queue
import time
import abc
import logging


logger = logging.getLogger(__name__)


protocol = {
    'flow_parameters': {
        'start_velocity': 50,
        'max_velocity': 1000,
        'stop_velocity': 500,
        'mode': 'tubing_stack',  # or 'tubing_flush'
        'extractionfactor': 1},
    'imaging': {
        'frames': 30000,
        't_exp': 100},
    'protocol_entries': [
        {'target': 'illumination', '$type': 'power',
         'value': 1},
        {'target': 'fluid', '$type': 'inject',
         'reservoir_id': 0, 'volume': 500},
        {'target': 'fluid', '$type': 'incubate',
         'duration': 120},
        {'target': 'fluid', '$type': 'inject',
         'reservoir_id': 1, 'volume': 500, 'velocity': 600},
        {'target': 'imaging', '$type': 'acquire',
         'frames': 10000, 't_exp': 100, 'round': 1},
        {'target': 'fluid', '$type': 'flush',
         'flushfactor': 1},
        {'target': 'fluid', '$type': 'await_acquisition'},
        {'target': 'fluid', '$type': 'inject',
         'reservoir_id': 20, 'volume': 500},
    ]}

protocol_fluid = [
    {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
    {'$type': 'incubate', 'duration': 120},
    {'$type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600},
    {'target': 'fluid', '$type': 'signal', 'value': 'fluid round 1 done'},
    {'$type': 'flush', 'flushfactor': 1},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
    {'$type': 'inject', 'reservoir_id': 14, 'volume': 500},
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
    def __init__(self, protocol, threadexchange):
        # super(threading.Thread, self).__init__()
        super().__init__()
        self.protocol = protocol
        print('starting system handler with protocol', self.protocol)
        self.txchange = threadexchange
        self.system = None  # is set in Handler subclasses

    def run(self):
        while ((not self.txchange['abort_flag'].is_set())
               and (not self.txchange['graceful_stop_flag'].is_set())):
            if self.txchange['start_protocol_flag'].is_set():
                self.run_protocol()
                while not self.poll_protocol_finished():
                    if ((self.txchange['abort_flag'].is_set()
                         or self.txchange['abort_protocol_flag'].is_set())):
                        return
                    time.sleep(.05)
                self.txchange['start_protocol_flag'].clear()

            self.work_queue()
            time.sleep(.05)

    def run_protocol(self):
        for i, step in enumerate(self.protocol):
            print('System performing step', i, ':', step)
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
            self.system.abort_protocol()
            return False
        elif self.txchange['pause_protocol_flag'].is_set():
            self.system.pause_protocol()
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
                if message in self.txchange[target]:
                    busy = False
                    break
            time.sleep(.05)

    def send_message(self, message):
        with self.txchange[self.target + '_lock']:
            self.txchange[self.target].append(message)

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
    def __init__(self, fluid_system, protocol, threadexchange):
        super().__init__(protocol['fluid'], threadexchange)
        self.target = 'fluid'
        self.system = fluid_system
        # assign the protocol - restructure this later on
        prot = {}
        prot['flow_parameters'] = protocol['flow_parameters']
        prot['protocol_entries'] = protocol['fluid']
        self.system._assign_protocol(prot)

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
    def __init__(self, imaging_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.target = 'imaging'
        self.system = imaging_system

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + '_lock']:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        pass


class IlluminationHandler(AbstractSystemHandler):
    def __init__(self, illumination_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.target = 'illumination'

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
        'imaging_lock': threading.Lock(),
        'imaging': [],
        'imaging_finished': threading.Event(),
        'illumination_lock': threading.Lock(),
        'illumination': [],
        'illumination_finished': threading.Event(),
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
        fluid_protocol = {
            'flow_parameters': protocol.get('flow_parameters', {}),
            'fluid': protocol.get('fluid', [])}
        self.fluid_handler = FluidHandler(
            fluid_system, fluid_protocol,
            self.threadexchange)

        self.imaging_system = imaging_system
        self.imaging_handler = ImagingHandler(
            imaging_system, protocol.get('imaging', []),
            self.threadexchange)

        self.illumination_system = illumination_system
        self.illumination_handler = IlluminationHandler(
            illumination_system, protocol.get('illumination', []),
            self.threadexchange)

        self.protocol = protocol

    def start_orchestration(self):
        self.fluid_handler.start()
        self.imaging_handler.start()
        self.illumination_handler.start()

    def start_protocol(self):
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

