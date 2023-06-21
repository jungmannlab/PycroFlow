"""
address are unique to the whole system (same as serial address)


"""
import pyHamiltonPSD as ham
import pyHamiltonMVP
from pyHamiltonMVP import MVP
from pyHamiltonPSD.util import SyringeMovement as SyrMov
from pyHamiltonPSD.util import SyringeTypes as SyrTypes
from pyHamiltonPSD.util import PSDTypes
import numpy as np
import unittest
from unittest.mock import patch, MagicMock, call
import logging
import sys
import time


logger = logging.getLogger()
logger.level = logging.DEBUG
logger.addHandler(logging.StreamHandler(sys.stdout))

is_connected = False


def connect(port, baudrate):
    assert isinstance(port, str)
    ham.connect(port, baudrate)
    global is_connected
    is_connected = True


def disconnect():
    ham.disconnect()
    global is_connected
    is_connected = False


"""Legacy system architecture:
* N valves for N*6+1 reservoirs ('valve_a')
* 1 syringe pump ('pump_a')
* sample
* 1 syringe pump ('pump_out')
* waste
"""
legacy_system_config = {
    'system_type': 'legacy',
    'valve_a': [
        {'address': 2, 'instrument_type': 'MVP', 'valve_type': '8-5'},
        {'address': 3, 'instrument_type': 'MVP', 'valve_type': '8-5'},
        {'address': 4, 'instrument_type': 'MVP', 'valve_type': '8-5'}],
    'valve_flush':
        {'address': 5, 'instrument_type': 'MVP', 'valve_type': '8-5'},
    'flush_pos': {'inject': 1, 'flush': 0},
    'pump_a':
        {'address': 1, 'instrument_type': '4', 'valve_type': 'Y',
         'syringe': '500u'},
    'pump_out': {
        'address': 0, 'instrument_type': '4', 'valve_type': 'Y',
        'syringe': '5.0m'},
    'reservoir_a': [
        {'id': 21, 'valve_pos': {2: 3, 3: 1, 4: 1}},
        {'id': 20, 'valve_pos': {2: 3, 3: 1, 4: 2}},
        {'id': 19, 'valve_pos': {2: 3, 3: 1, 4: 3}},
        {'id': 18, 'valve_pos': {2: 3, 3: 1, 4: 4}},
        {'id': 17, 'valve_pos': {2: 3, 3: 1, 4: 5}},
        {'id': 16, 'valve_pos': {2: 3, 3: 1, 4: 6}},
        {'id': 15, 'valve_pos': {2: 3, 3: 1, 4: 7}},
        {'id': 14, 'valve_pos': {2: 3, 3: 1, 4: 8}},
        {'id': 13, 'valve_pos': {2: 3, 3: 2}},
        {'id': 12, 'valve_pos': {2: 3, 3: 3}},
        {'id': 11, 'valve_pos': {2: 3, 3: 4}},
        {'id': 10, 'valve_pos': {2: 3, 3: 5}},
        {'id': 9, 'valve_pos': {2: 3, 3: 6}},
        {'id': 8, 'valve_pos': {2: 3, 3: 7}},
        {'id': 7, 'valve_pos': {2: 3, 3: 8}},
        {'id': 6, 'valve_pos': {2: 1}},
        {'id': 5, 'valve_pos': {2: 2}},
        {'id': 4, 'valve_pos': {2: 4}},
        {'id': 3, 'valve_pos': {2: 5}},
        {'id': 2, 'valve_pos': {2: 6}},
        {'id': 1, 'valve_pos': {2: 7}},
        {'id': 0, 'valve_pos': {2: 8}},
        ],
    'special_names': {
        'flushbuffer_a': 21,  # defines the reservoir id with the buffer that can be used for flushing should be at the end of multiple MVPs
        },
}

# description of tubing volumes between
# reservoirs -> confluxes(if present) -> pumps
# connections are set fixed for each system architecture
legacy_tubing_config = {
    (21, 'pump_a'): 365,
    (20, 'pump_a'): 365,
    (19, 'pump_a'): 365,
    (18, 'pump_a'): 365,
    (17, 'pump_a'): 365,
    (16, 'pump_a'): 365,
    (15, 'pump_a'): 365,
    (14, 'pump_a'): 365,
    (13, 'pump_a'): 260,
    (12, 'pump_a'): 260,
    (11, 'pump_a'): 260,
    (10, 'pump_a'): 260,
    (9, 'pump_a'): 260,
    (8, 'pump_a'): 260,
    (7, 'pump_a'): 260,
    (6, 'pump_a'): 215,
    (5, 'pump_a'): 215,
    (4, 'pump_a'): 215,
    (3, 'pump_a'): 215,
    (2, 'pump_a'): 215,
    (1, 'pump_a'): 215,
    (0, 'pump_a'): 215,
    ('pump_a', 'valve_flush'): 156,
    ('valve_flush', 'sample'): 256,
}

"""the 'flattened protocol' (in contrast to the 'aggregated' Exchange/MERPAINT protocol
specified by the user)
This is the protocol generated by the 'upper system' from the aggregated protocol.
Protocol entries are stepped through in the upper system, calling 'LegacyArchitecture'
for the fluid-control steps.
"""
protocol = {
    'flow_parameters': {
        'start_velocity': 50,
        'max_velocity': 1000,
        'stop_velocity': 500,
        'mode': 'tubing_stack',  # or 'tubing_flush'
        'extractionfactor': 1,},
    'imaging': {
        'frames': 30000,
        't_exp': 100},
    'protocol_entries': [
        {'type': 'inject', 'reservoir_id': 0, 'volume': 500},
        {'type': 'incubate', 'duration': 120},
        {'type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600},
        {'type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
        {'type': 'flush', 'flushfactor': 1},
        {'type': 'await_acquisition'},
        {'type': 'inject', 'reservoir_id': 20, 'volume': 500},   # for more commplex system: 'mix'
    ]}


def map_valve_type(valve):
    vmap = {
        '1': '1', 'Y': '1',
        '2': '2', 'T': '2', '3-3': '2',
        '3': '3', '3-5': '3',
        '4': '4', '4-2': '4', '4-5': '4',
        '5': '5', '6-5': '5',
        '6': '6', '8-5': '6'}
    return vmap[valve]


class Reservoir():
    """Representation of a fluid reservoir
    """

    def __init__(self, id, valve_pos):
        self.id = id
        self.valve_positions = valve_pos

    def get_valve_positions(self):
        return self.valve_positions


class Valve():
    def __init__(self, address, instrument_type='4', valve_type='1'):
        """
        Args:
            addresss : char
                instrument address as set on address switch, '0' to 'F'
            instrument_type : str
                one of ['MVP']
            valve_type : str
                one of (as described in PSD3 manual, p. 15)
                val: alt   : description                           DIP Switch
                '1': 'Y'   : 3-Port Y Valve                        OFF OFF OFF
                '2': 'T', '3-3' : T-Port Valve                     ON  OFF OFF
                '3': '3-5' : 3-Port Distribution Valve             OFF ON  OFF
                '4': '4-2', '4-5': 4-Port Dist / 4-Port Wash Valve OFF OFF ON
                '5': '6-5' : 6-Port Distribution Valve             OFF ON  ON
                '6': '8-5' : 8-Port Distribution Valve             ON  ON  OFF
                Hopefully same as for PSD?
                However, it is unclear whether these values actually need to be
                set, as they are set on the DIP switch (PSD manual, p.15)

        """
        assert instrument_type in ['MVP']

        if instrument_type == 'MVP':
            self.mvp = MVP(str(address), instrument_type)

        ham.communication.sendCommand(
            self.mvp.asciiAddress,
            self.mvp.command.initializeValve()
            + self.mvp.command.enableValveMovement()
            + self.mvp.command.enableHFactorCommandsAndQueries()
            + self.mvp.command.executeCommandBuffer())
        valve_type = map_valve_type(valve_type)

    def set_valve(self, pos):
        """Sets the valve position of the PSD.
        Args:
            pos : str
                one of 'in' or 'out', or a position between 1 and 8
        """
        assert pos in ['in', 'out', *list(range(1, 9))]
        if pos == 'in':
            cmd = self.mvp.command.moveValveToInputPosition()
        elif pos == 'out':
            cmd = self.mvp.command.moveValveToOutputPosition()
        else:
            cmd = self.mvp.command.moveValveInShortestDirection(pos)
        ham.communication.sendCommand(
            self.mvp.asciiAddress,
            cmd + self.mvp.command.executeCommandBuffer(),
            waitForPump=False)

    def get_status(self):
        """polls and returns the status
        """
        return ham.communication.sendCommand(
            self.mvp.asciiAddress, 'Q', waitForPump=False)


class Pump():
    curr_vol = 0

    def __init__(self, address, syringe,
                 instrument_type='4', valve_type='1', resolution_mode=1):
        """
        Args:
            addresss : char
                instrument address as set on address switch, '0' to 'F'
            syringe : char
                The syringe type/volume
                '12.5u', '25u', '50u', '100u', '125u', '250u', '500u',
                '1.0m', '2.5m', '5.0m', '10m', '25m', '50m'
            instrument_type : str
                one of
                '4' (PSD4), '6' (PSD6), '4sf' (PSD4 smooth flow),
                '6sf' (PSD6 smooth flow)
            valve_type : str
                one of
                '1': 3-Port Y Valve
                '2': T-Port Valve
                '3': 3-Port Distribution Valve
                '4': 4-Port Distribution Valve 4-Port Wash Valve
                '5': 6-Port Distribution Valve
                '6': 8-Port Distribution Valve
                However, it is unclear whether these values actually need to be
                set, as they are set on the DIP switch (PSD manual, p.15)
            resolution_mode : int
                0: standard resolution (0-3000 steps)
                1: high resolution mode (0-24000 steps)
                Not sure this can be freely chosen or depends on hardware
                (but it is not the distinction between PSD4 and PSD4 smooth
                flow!)
        """
        assert instrument_type in [member.value for member in PSDTypes]
        assert syringe in [member.value for member in SyrTypes]
        self.psd = ham.PSD(str(address), instrument_type)
        ham.communication.sendCommand(
            self.psd.asciiAddress,
            'Z' + self.psd.command.enableHFactorCommandsAndQueries()
            + self.psd.command.executeCommandBuffer())
        # ham.communication.sendCommand(
        #     self.psd.asciiAddress,
        #     self.psd.command.standardHighResolutionSelection(resolution_mode),
        #     waitForPump=True)
        result = ham.communication.sendCommand(
            self.psd.asciiAddress,
            self.psd.command.syringeModeQuery(),
            waitForPump=True)
        resolution = result[3:4]
        self.psd.setResolution(int(resolution))
        self.psd.calculateSteps()
        self.psd.calculateSyringeStroke()
        self.psd.setVolume(syringe)

        # syringe volume in µl
        self.syringe_volume = float(syringe[:-1])
        if syringe[-1] == 'm':
            self.syringe_volume *= 1000

    def dispense(self, vol, velocity=None, waitForPump=False):
        if velocity is not None:
            cmd = self.psd.command.setMaximumVelocity(velocity)
        else:
            cmd = ''
        cmd += self.psd.command.syringeMovement(SyrMov.relativeDispense.value, vol)
        cmd += self.psd.command.executeCommandBuffer()
        ham.communication.sendCommand(
            self.psd.asciiAddress, cmd, waitForPump=waitForPump)
        self.curr_vol -= vol

    def pickup(self, vol, velocity=None, waitForPump=False):
        if velocity is not None:
            cmd = self.psd.command.setMaximumVelocity(velocity)
        else:
            cmd = ''
        cmd += self.psd.command.syringeMovement(SyrMov.relativePickup.value, vol)
        cmd += self.psd.command.executeCommandBuffer()
        ham.communication.sendCommand(
            self.psd.asciiAddress, cmd, waitForPump=waitForPump)
        self.curr_vol += vol

    def set_velocity(self, start_velocity, max_velocity, stop_velocity):
        """Set movement start, max, and stop velocities
        Args:
            start_velocity : int
                50-1000
            max_velocity : int
                2-5800
            stop_velocity : int
                50-2700
        """
        ham.communication.sendCommand(
            self.psd.asciiAddress,
            + self.psd.command.setStartVelocity(start_velocity)
            + self.psd.command.setMaximumVelocity(max_velocity)
            + self.psd.command.setStopVelocity(stop_velocity)
            + self.psd.command.executeCommandBuffer(),
            waitForPump=False)

    def set_valve(self, pos):
        """Sets the valve position of the PSD.
        Args:
            pos : str
                one of 'in' or 'out'
        """
        assert pos in ['in', 'out']
        if pos == 'in':
            cmd = self.psd.command.moveValveToInputPosition()
        else:
            cmd = self.psd.command.moveValveToOutputPosition()
        ham.communication.sendCommand(
            self.psd.asciiAddress,
            cmd + self.psd.command.executeCommandBuffer(),
            waitForPump=True)

    def wait_until_done(self):
        """Waits until the pump is done moving.
        Using pyHam functionality for now, therefor eno timeout
        Args:
            timeout : float
                timeout in s
        """
        ham.communication.sendCommand(
            self.psd.asciiAddress, 'Q', waitForPump=True)
        # tic = time.time()
        # while time.time() < tic + timeout:
        #     time.sleep(.05)

    def get_status(self):
        """polls and returns the status
        """
        return ham.communication.sendCommand(
            self.psd.asciiAddress, 'Q', waitForPump=False)

    def stop_current_move(self):
        """Stops the current movement. The pump needs to be
        reinitialized after this.
        """
        ham.communication.sendCommand(
            self.psd.asciiAddress,
            self.psd.command.terminateCommandBuffer(),
            waitForPump=False)


class LegacyArchitecture():
    """Represents the Legacy Architecture, with many valves and
    reservoirs, connected to an input syringe pump, connected to
    the sample, connected to an output/waste syringe pump.
    """
    valve_a = {}
    valve_flush = None
    pump_a = None
    pump_out = None
    tubing_config = {}
    reservoir_a = {}
    # maps reservoir ids to the fluid paths (legacy only has 'a')
    reservoir_paths = {}
    protocol = []
    last_protocol_entry = -1

    extractionfactor = 1

    def __init__(self, system_config, tubing_config, port='4', baudrate=9600):
        self._assign_system_config(system_config)
        self._assign_tubing_config(tubing_config)
        global is_connected
        if not is_connected:
            if 'COM' in port:
                port = port[3:]
            connect(port, baudrate)
        self._test_communication()

    def _assign_system_config(self, config):
        """Assign a system configuration
        Args:
            config : dict
                the system configuration
        """
        assert config['system_type'] == 'legacy'
        for vconfig in config['valve_a']:
            self.valve_a[vconfig['address']] = Valve(**vconfig)
        for rconfig in config['reservoir_a']:
            self.reservoir_a[rconfig['id']] = Reservoir(**rconfig)
            self.reservoir_paths[rconfig['id']] = 'a'
        self.valve_flush = Valve(**config['valve_flush'])
        self.pump_a = Pump(**config['pump_a'])
        self.pump_out = Pump(**config['pump_out'])

        self.special_names = config['special_names']
        self.flush_pos = config['flush_pos']

    def _assign_protocol(self, protocol):
        self.protocol = protocol['protocol_entries']
        self.flow_parameters = protocol['flow_parameters']

    def _assign_tubing_config(self, config):
        self.tubing_config = config

    def _test_communication(self):
        """Asks all devives for status to check whether they are connected
        """
        conn_dev = {}
        result = self.pump_a.get_status()
        conn_dev['pump_a'] = (result != '')
        result = self.pump_out.get_status()
        conn_dev['pump_out'] = (result != '')
        for v_id, valve in self.valve_a.items():
            result = valve.get_status()
            conn_dev['valve_a-' + str(v_id)] = (result != '')
        all_connected = all(conn_dev.values())
        if not all_connected:
            logger.warning('Not all devices are connected: ' + str(conn_dev))

    def execute_protocol_entry(self, i):
        """Execute a protocol entry.
        When not executing the next protocol entry but jumpint to one
        out of order, the tubing-'stack' needs to be re-assembled.

        A protocol entry consists of:
            type, parameters.
        e.g.
            type='inject', reserviorID, volume, speed, extractionfactor
            type='wait_image',
            type='wait_time', duration
        """
        if self.flow_parameters['mode'] == 'tubing_stack':
            if (self.last_protocol_entry != i - 1) or (i == 0):
                self._assemble_tubing_stack(i)
            for reservoir_id, vol in self.tubing_stack[i]:
                self._set_valves(reservoir_id)
                self._inject(vol)
            self.last_protocol_entry = i
        elif self.flow_parameters['mode'] == 'tubing_flush':
            # this way, we flush (1+flushfactor)
            # and also through sample. But that shouldn't matter for now.
            self._flush()
            self.execute_single_protocol_entry(i)
        else:
            raise NotImplmentedError('Mode ' + self.flow_parameters['mode'])

    def execute_single_protocol_entry(self, i):
        """Execute only one single entry of the protocol; do not fill the
        tubing with the (potentially precious) later protocol entry fluids,
        but with buffer.
        """
        pentry = self.protocol[i]
        if pentry['type'] == 'inject':
            flush_volume = self._calc_vol_to_inlet(pentry['reservoir_id'])
            injection_volume = pentry['volume']
            # first, set up the volume required
            self._set_valves(pentry['reservoir_id'])
            self._inject(injection_volume, pentry['speed'])
            # afterwards, flush in buffer to get the pentry volume to the sample
            self._set_valves(self.special_names['flushbuffer_a'])
            self._inject(flush_volume, pentry['speed'])

        self.last_protocol_entry = -1  # tubing full of buffer, cannot simply proceed

    def _assemble_tubing_stack(self, i):
        """Assemble the 'column' of different fluids stacked into the tubing.
        In an efficient delivery, when delivering fluid of step i into the
        sample, the tubing already needs to be switched to fluids of later steps.
        Tubing stack is only used in flow parameter mode 'tubing_stack', not
        in 'tubing_flush'

        Args:
            i : int
                the protocol step to start with
        Returns:
            column : dict
                keys: protocol step
                values: list of injection tuples with (reservoir_a_id, volume)
        """
        column = {}
        nsteps = len(self.protocol[i:])
        reservoirs = np.zeros(nsteps + 1, dtype=np.int32)
        volumes = np.zeros(nsteps + 1, dtype=np.float64)

        for idx, pentry in enumerate(self.protocol[i:]):
            if pentry['type'] == 'inject':
                reservoirs[idx] = pentry['reservoir_id']
                volumes[idx] = pentry['volume']
            elif pentry['type'] == 'flush':
                # flush step is meant only for mode 'tubing_flush', this 
                # _assemble_tubing_stack si meant for mode 'tubing_stack'
                # however, let's add this; will go through sample and not
                # through the flush valve, though.
                reservoirs[idx] = self.special_names['flushbuffer_a']
                flushfactor = pentry.get('flushfactor', 1)
                tubing_vol = (
                    self.tubing_config[(self.special_names['flushbuffer_a'], 'pump_a')]
                    + self.tubing_config[('pump_a', 'valve_flush')])
                volumes[idx] = flushfactor * tubing_vol
        reservoirs[-1] = self.special_names['flushbuffer_a']
        volumes[-1] = self._calc_vol_to_inlet(reservoirs[-1])

        volumes_cum = np.cumsum(volumes)

        for idx, step in enumerate(range(i, i + nsteps)):
            injection_tuples = []
            if idx == 0:
                vol = (
                    volumes[idx]
                    + self._calc_vol_to_inlet(reservoirs[idx]))
            else:
                vol = (
                    volumes[idx]
                    + self._calc_vol_to_inlet(reservoirs[idx])
                    - self._calc_vol_to_inlet(reservoirs[idx - 1]))
            vol_rest = vol
            cum_start = np.argwhere(volumes_cum > 0).flatten()[0]
            try:
                cum_stop = np.argwhere(volumes_cum > vol).flatten()[0] + 1
            except IndexError:
                cum_stop = len(volumes_cum)
            for cum_idx in range(cum_start, cum_stop):
                vol_step = min([vol_rest, volumes_cum[cum_idx]])
                if vol_step == 0:
                    continue
                injection_tuples.append(
                    tuple([reservoirs[cum_idx], vol_step]))
                volumes_cum -= vol_step
                vol_rest -= vol_step
            column[step] = injection_tuples
        self.tubing_stack = column

    def _calc_vol_to_inlet(self, reservoir_id):
        """Calculates the tubing volume between reservoir and inlet needle
        from the tubing configuration. This is legacy system specific
        """
        vol_res_pump_a = self.tubing_config[(reservoir_id, 'pump_a')]
        vol_pump_a_valveflush = self.tubing_config[('pump_a', 'valve_flush')]
        vol_valveflush_inlet = self.tubing_config[('valve_flush', 'sample')]
        return vol_res_pump_a + vol_pump_a_valveflush + vol_valveflush_inlet

    def _set_valves(self, reservoir_id):
        """Set the valves to access the reservoir specified
        """
        if self.reservoir_paths[reservoir_id] == 'a':
            valve_positions = self.reservoir_a[reservoir_id].get_valve_positions()
            for valve, pos in valve_positions.items():
                self.valve_a[valve].set_valve(pos)
        else:
            raise NotImplmentedError('Legacy system only has fluid path "a".')

    def _flush(self, flushfactor=1):
        """Flush the tubing until the flush valve with the flush buffer.
        This makes sure there are no residual molecules of one injection fluid
        in tubing or syringe for the next injection step

        Args:
            flushfactor : float
                fold of tubing volume to flush
        """
        self.valve_flush.set_valve(self.flush_pos['flush'])
        self.set_valves(self.special_names['flushbuffer_a'])
        tubing_vol = (
            self.tubing_config[(self.special_names['flushbuffer_a'], 'pump_a')]
            + self.tubing_config[('pump_a', 'valve_flush')])
        self._dispense(tubing_vol * flushfactor)

    def _dispense(self, pump, vol, velocity=None):
        """Dispense fluid according to the currently set valve positions.
        This routine is meant for flushing or for calibration. For experiment
        steps, use _inject.
        Args:
            pump : Pump
                the pump to use
            vol : float
                the volume to pump
            velocity : int
                the flow velocity of injection in µl/min
        """
        if velocity is None:
            velocity = self.flow_parameters['max_velocity']
        if pump.curr_vol > 0:
            pump.set_valve('out')
            pump.dispense(pump.curr_vol, velocity)
            pump.wait_until_done()

        volume_quant = pump.syringe_volume
        nr_pumpings = int(vol // volume_quant)
        pump_volumes = volume_quant * np.ones(nr_pumpings + 1)
        pump_volumes[-1] = vol % volume_quant

        for pump_volume in pump_volumes:
            pump.set_valve('in')
            pump.pickup(pump_volume, velocity, waitForPump=True)
            pump.set_valve('out')
            pump.dispense(pump_volume, velocity, waitForPump=True)

    def _inject(self, vol, velocity=None, extractionfactor=None):
        """Inject volume from the currently selected reservoir into
        the sample with a given flow velocity. Simultaneously, the extraction
        pump extracts the same volume.

        Args:
            vol : float
                the volume to inject in µl
            velocity : float
                the flow velocity of the injection in µl/min
            extractionfactor : float
                the factor of flow speeds of extraction vs injection.
                a non-perfectly calibrated system may result in less volume
                being extracted than injected, leading to spillage. To prevent
                this, the extraction needle could be positioned higher, and
                extraction performed faster than injection
        """
        if velocity is None:
            velocity = self.flow_parameters['max_velocity']
        if extractionfactor is None:
            extractionfactor = self.flow_parameters['extractionfactor']
        velocity_out = int(
            extractionfactor * velocity
            * self.pump_a.syringe_volume / self.pump_out.syringe_volume)

        self.valve_flush.set_valve(self.flush_pos['inject'])
        if self.pump_a.curr_vol > 0:
            self.pump_a.set_valve('out')
            self.pump_out.set_valve('in')
            self.pump_a.dispense(self.pump_a.curr_vol, velocity)
            self.pump_out.pickup(
                self.pump_a.curr_vol * extractionfactor,
                velocity_out)
            self.pump_a.wait_until_done()
            self.pump_out.wait_until_done()

        volume_quant = max([
            self.pump_a.syringe_volume,
            self.pump_out.syringe_volume / extractionfactor])
        nr_pumpings = int(vol // volume_quant)
        pump_volumes = volume_quant * np.ones(nr_pumpings + 1)
        pump_volumes[-1] = vol % volume_quant

        for pump_volume in pump_volumes:
            self.pump_a.set_valve('in')
            self.pump_out.set_valve('out')
            self.pump_a.pickup(pump_volume, velocity, waitForPump=False)
            self.pump_out.dispense(
                self.pump_out.curr_vol * extractionfactor,
                velocity_out, waitForPump=False)
            self.pump_a.wait_until_done()
            self.pump_out.wait_until_done()

            self.pump_a.set_valve('out')
            self.pump_out.set_valve('in')
            self.pump_a.dispense(pump_volume, velocity, waitForPump=False)
            self.pump_out.pickup(
                pump_volume * extractionfactor,
                velocity_out, waitForPump=False)
            self.pump_a.wait_until_done()
            self.pump_out.wait_until_done()

        self.pump_out.set_valve('out')
        self.pump_out.dispense(
            self.pump_out.curr_vol,
            velocity_out, waitForPump=False)
        self.pump_out.wait_until_done()

    def fill_tubings(self):
        """Fill the tubings with the liquids of their reservoirs.
        """
        for res_id, res in self.reservoir_a.items():
            # take care of the flushbuffer last
            if res_id == self.special_names['flushbuffer_a']:
                continue
            self._set_valves(res_od)
            vol = self.tubing_config[(res_id, 'pump_a')]
            self._dispense(self.pump_a, vol)  # could use only input pump instead

        # now, flush everything with the flushbuffer
        self._set_valves(self.special_names['flushbuffer_a'])
        vol = (
            self.tubing_config[(self.special_names['flushbuffer_a'], 'pump_a')]
            + self.tubing_config[('pump_a', 'valve_flush')]
            + self.tubing_config[('valve_flush', 'sample')])
        self._dispense(self.pump_a, vol)

    # def calibrate_tubings(self, max_tubing_vol=500):
    #     """Measure the tubing volumes

    #     Args:
    #         max_tubing_vol : float
    #             the maximum possible tubing volume; this is used
    #             to pre-fill the tubings
    #     """
    #     print('filling tubings. Please fill tubes with > {:.1f} µl water'.format(max_tubing_vol))
    #     max_tubing_config = {}
    #     for res_id, res in self.reservoir_a.items():
    #         max_tubing_config[(res_id, 'pump_a')] = max_tubing_vol
    #     max_tubing_config[('pump_a', 'valve_flush')] = max_tubing_vol
    #     max_tubing_config[('valve_flush', 'sample')] = max_tubing_vol
    #     self.tubing_config = max_tubing_config
    #     self.fill_tubings()
    #     # todo: etc



"""
Diluter system architecture:
* a1) N valves for N*6+1 secondary probe reservoirs ('valve_a')
* a2) 1 syringe pump for reservoirs ('pump_a')

* b) 1 syringe pump for 1 buffer ('pump_b')
* c1) 1 valve for selecting imagers ('valve_c')
* c2) 1 syringe pump for pumping imagers ('pump_b')
* bc) 1 passive connection combining a and b ('conflux_bc')

* abc) 1 valve switching between ab and c ('conflux_abc')

* sample

* 1 syringe pump ('pump_out')
* waste
"""
diluter_system_config = {
    'system_type': 'diluter',
    'valve_a': [
        {'address': 0, 'intrument_type': 'MVP', 'valve_type': '8-way'},
        {'address': 1, 'intrument_type': 'MVP', 'valve_type': '8-way'}],
    'valve_c': [
        {'address': 2, 'intrument_type': 'MVP', 'valve_type': '8-way'}],
    'reservoir_a': [
        {'id': 0, 'valve_pos': {0: 1, 1: 1, 7: 1}},
        {'id': 1, 'valve_pos': {0: 1, 1: 2, 7: 1}},
        {'id': 2, 'valve_pos': {0: 3, 1: 1, 7: 1}}],
    'reservoir_b': [
        {'id': 3, 'valve_pos': {7: 2}}],
    'reservoir_c': [
        {'id': 4, 'valve_pos': {2: 2, 7: 2}},
        {'id': 5, 'valve_pos': {2: 3, 7: 2}}],
    'pump_a': {'address': 3, 'instrument_type': 'PSD4', 'valve_type': 'Y', 'syringe': '500µl'},
    'pump_b': {'address': 4, 'instrument_type': 'PSD4', 'valve_type': 'Y', 'syringe': '500µl'},
    'pump_c': {'address': 5, 'instrument_type': 'PSD4', 'valve_type': 'Y', 'syringe': '50µl'},
    'pump_out': {'address': 6, 'instrument_type': 'PSD4', 'valve_type': 'Y', 'syringe': '500µl'},
    'conflux_bc': {'instrument_type': 'passive'},
    'conflux_abc': {'address': 7, 'instrument_type': 'MVP', 'valve_type': '4-way'},
}

diluter_tubing_config = {
    (0, 'pump_a'): 153.2,
    (1, 'pump_a'): 151.8,
    ('pump_a', 'conflux_abc'): 30.7,
    (3, 'pump_b'): 11.8,
    (4, 'pump_c'): 15.8,
    (5, 'pump_c'): 16.8,
    ('pump_b', 'conflux_bc'): 12,
    ('pump_c', 'conflux_bc'): 13,
    ('conflux_bc', 'conflux_abc'): 31,
    ('conflux_abc', 'sample'): 28
}


class DiltuterArchitecture(LegacyArchitecture):
    """
    """

    def __init__(self):
        pass

    def mix_injection(self):
        """Same as inject, only two input syringes have to be actuated simultaneously,
        with the correct relative flow speed. More a matter of the experiment protocol
        specifying the pump(s) to use
        """
        pass



experiment_config = {
    'reservoir_names': {
        0: 'Buffer B+',
        1: 'Imager 50pM',
    }
}


class LegacyArchitectureTest(unittest.TestCase):
    def setUp(self):
        test_system_config = {
            'system_type': 'legacy',
            'valve_a': [
                {'address': 0, 'instrument_type': 'MVP', 'valve_type': '8-5'},
                {'address': 1, 'instrument_type': 'MVP', 'valve_type': '8-5'},
                ],
            'valve_flush': {'address': 4, 'instrument_type': 'MVP', 'valve_type': '8-5'},
            'flush_pos': {'inject': 1, 'flush': 0},
            'pump_a': {'address': 2, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '500u'},
            'pump_out': {'address': 3, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '5.0m'},
            'reservoir_a': [
                {'id': 0, 'valve_pos': {0: 3, 1: 2}},
                {'id': 1, 'valve_pos': {0: 2, 1: 2}},
                {'id': 3, 'valve_pos': {0: 2, 1: 4}},
                ],
            'special_names': {
                'flushbuffer_a': 3,  # defines the reservoir id with the buffer that can be used for flushing}
                }
            }
        test_tubing_config = {
            (0, 'pump_a'): 0,
            (1, 'pump_a'): 0,
            (3, 'pump_a'): 0,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 0,
        }
        test_protocol = {
            'flow_parameters': {
                'start_velocity': 50,
                'max_velocity': 1000,
                'stop_velocity': 500,
                'mode': 'tubing_stack',
                'extractionfactor': 2},
            'imaging': {
                'frames': 30000,
                't_exp': 100},
            'protocol_entries': [
                {'type': 'inject', 'reservoir_id': 0, 'volume': 500},
                {'type': 'inject', 'reservoir_id': 1, 'volume': 200, 'velocity': 600},
                {'type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
                {'type': 'inject', 'reservoir_id': 0, 'volume': 300},   # for more commplex system: 'mix'
            ]}
        patch_send_command = patch('pyHamiltonPSD.communication.sendCommand', create=True)
        patch_send_command.start()
        self.addCleanup(patch_send_command.stop)

        patch_connect = patch('pyHamiltonPSD.communication.initializeSerial', create=True)
        patch_connect.start()
        self.addCleanup(patch_connect.stop)

        patch_connect = patch('pyHamiltonPSD.initializeSerial', create=True)
        patch_connect.start()
        self.addCleanup(patch_connect.stop)

        patch_disconnect = patch('pyHamiltonPSD.communication.disconnectSerial', create=True)
        patch_disconnect.start()
        self.addCleanup(patch_disconnect.stop)

        # patch_pump = patch(__name__ + '.Pump')
        # patch_pump.start()
        # self.addCleanup(patch_pump.stop)

        # patch_valve = patch(__name__ + '.Valve')
        # patch_valve.start()
        # self.addCleanup(patch_valve.stop)

        # patch_res = patch(__name__ + '.Reservoir', autospec=True)
        # patch_res.start()
        # self.addCleanup(patch_res.stop)

        self.va = LegacyArchitecture(test_system_config, test_tubing_config)
        self.va._assign_protocol(test_protocol)

        # print(self.va.pump_a.call_args_list)
        # print(self.va.pump_out.call_args_list)

    def test_vol_to_inlet(self):
        # check vol to inlet calculation
        vol = self.va._calc_vol_to_inlet(1)
        # print(vol)
        self.assertTrue(vol == 0)
        # print(ham.communication.sendCommand.call_args_list)
        # assert False

    def test_tubing_stack_1(self):
        # check tubing column without volume in tubings
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.)],
            1: [(1, 200.)],
            2: [],
            3: [(0, 300.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_2(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            (0, 'pump_a'): 0,
            (1, 'pump_a'): 0,
            (3, 'pump_a'): 0,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 100,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.), (1, 100.)],
            1: [(1, 100.), (0, 100.)],
            2: [],
            3: [(0, 200.), (3, 100.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_tubing_stack_3(self):
        # check tubing column with volume in tubings
        test_tubing_config_2 = {
            (0, 'pump_a'): 100,
            (1, 'pump_a'): 300,
            (3, 'pump_a'): 200,
            ('pump_a', 'valve_flush'): 0,
            ('valve_flush', 'sample'): 0,
        }
        self.va._assign_tubing_config(test_tubing_config_2)
        self.va._assemble_tubing_stack(0)
        # print(self.va.tubing_stack)

        # as no tubing volume is assigned, the tubing column
        # matches the single steps
        tubing_stack_expected = {
            0: [(0, 500.), (1, 100.)],
            1: [(1, 100.), (0, 300.)],
            2: [],
            3: [(3, 200.)],
        }
        # print('expected', tubing_stack_expected)
        # print('actual', self.va.tubing_stack)
        self.assertDictEqual(tubing_stack_expected, self.va.tubing_stack)

    def test_set_valve(self):
        """
        test the reservoir setting
            'reservoir_a': [
                {'id': 0, 'valve_pos': {0: 3, 1: 2}},
                {'id': 1, 'valve_pos': {0: 2, 1: 2}},
                {'id': 3, 'valve_pos': {0: 2, 1: 4}},
        """
        ham.communication.sendCommand.reset_mock()
        self.va._set_valves(0)
        # logger.debug(ham.communication.sendCommand.call_args_list)
        ham.communication.sendCommand.assert_has_calls([
            call('1', 'h26003R', waitForPump=False),
            call('2', 'h26002R', waitForPump=False)])

        ham.communication.sendCommand.reset_mock()
        self.va._set_valves(3)
        ham.communication.sendCommand.assert_has_calls([
            call('1', 'h26002R', waitForPump=False),
            call('2', 'h26004R', waitForPump=False)])

    def test_inject(self):
        """Test system injection
        """
        ham.communication.sendCommand.reset_mock()
        self.va._inject(10)
        # logger.debug(ham.communication.sendCommand.call_args_list)
        ham.communication.sendCommand.assert_has_calls([
            call('5', 'h26001R', waitForPump=False),
            call('3', 'IR', waitForPump=True),
            call('4', 'OR', waitForPump=True),
            call('3', 'V1000P480R', waitForPump=False),
            call('4', 'V200D0R', waitForPump=False),
            call('3', 'Q', waitForPump=True),
            call('4', 'Q', waitForPump=True),
            call('3', 'OR', waitForPump=True),
            call('4', 'IR', waitForPump=True),
            call('3', 'V1000D480R', waitForPump=False),
            call('4', 'V200P96R', waitForPump=False),
            call('3', 'Q', waitForPump=True),
            call('4', 'Q', waitForPump=True),
            call('4', 'OR', waitForPump=True),
            call('4', 'V200D96R', waitForPump=False),
            call('4', 'Q', waitForPump=True)])


if __name__ == '__main__':
    unittest.main()

    # # do a test run
    # connect('4', 9600)
    # arch = LegacyArchitecture(legacy_system_config, legacy_tubing_config)
    # arch._assign_protocol(protocol)

    # input('press any key to start')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '.format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '.format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '.format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '.format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # # input('press any key to perform next step')
    # # arch.perform_next_protocol_entry()
    # # print('performing protocol entry {:d}: '.format(arch.curr_protocol_entry),
    # #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # print('done')
    # disconnect()

    # patch_res = patch(__name__ + '.Reservoir', autospec=True)
    # patch_res.start()