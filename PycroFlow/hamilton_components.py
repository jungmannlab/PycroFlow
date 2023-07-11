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

    @property
    def nvalves(self):
        return len(self.valve_positions.keys())


class ReservoirDict():
    """A data structure containing multiple Reservoirs
    """

    def __init__(self):
        self.reservoirs = {}

    def add(self, reservoir):
        """Add a reservoir to the dict

        Args:
            reservoir : Reservoir
                the reservoir to add
        """
        self.reservoirs[reservoir.id] = reservoir

    @property
    def len(self):
        """Get the number of reservoirs
        """
        return len(self.reservoirs.keys())

    def items(self):
        """Get the entry items
        """
        return self.reservoirs.items()

    def get_reservoir_nvalves(self, res):
        """Get the number of valves between a reservoir and the pump
        Args:
            res : int
                the reservoir id
        Returns:
            nvalves : int
                the number of valves between this reservoir and the pump
        """
        if isinstance(res, str):
            # if coming from the input funciton, this might be a string
            res = int(res)
        return self.reservoirs[res].nvalves

    def get_reservoir_valve_positions(self, res):
        """Get the valve position dict for a given reservoir
        Args:
            res : int
                the reservoir id

        Returns:
            valve_positions : dict
                the dict of valve address : valve position
        """
        if isinstance(res, str):
            # if coming from the input funciton, this might be a string
            res = int(res)
        return self.reservoirs[res].get_valve_positions()


class TubingConfig():
    """A data structure describing the tubing configuration
    """

    def __init__(self, config):
        """
        Args:
            config : dict
                the tubing configuration
        """
        self.config = config

    def set_special_names(self, special_names):
        """Set special reservoir names
        Args:
            special_names : dict
                map from a special name to reservoir id
        """
        self.special_names = special_names

    def get(self, start, end):
        """Get an entry volume
        Args:
            start : str
                the starting point
            end : str
                the end point
        """
        return self.config[(start, end)]

    def get_reservoir_to_pump(self, res_id, pump_name):
        """convenience function to get the volume from reservoir
        to pump
        Args:
            res_id : int or str
                the reservoir id
                or the special name for it
                or the dict key (e.g.'R2')
            pump_name : char
                the pump name (e.g. 'a' for 'pump_a')
            vol : float
                the volume in µl
        """
        if isinstance(res_id, str):
            if res_id in self.special_names.keys():
                res = 'R' + str(self.special_names[res_id])
            else:
                res = res_id
        else:
            res = 'R' + str(res_id)
        return self.config[(res, 'pump_' + pump_name)]

    def set_reservoir_to_pump(self, res_id, pump_name, vol):
        """convenience function to get the volume from reservoir
        to pump
        Args:
            res_id : int or str
                the reservoir id
                or the special name for it
                or the dict key (e.g.'R2')
            pump_name : char
                the pump name (e.g. 'a' for 'pump_a')
            vol : float
                the volume in µl
        """
        if isinstance(res_id, str):
            if res_id in self.special_names.keys():
                res = 'R' + str(self.special_names[res_id])
            else:
                res = res_id
        else:
            res = 'R' + str(res_id)
        self.config[(res, 'pump_' + pump_name)] = vol


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
    current_volume = 0
    target_volume = 0

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
            + self.psd.command.executeCommandBuffer(),
            waitForPump=True)
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
        cmd += self.psd.command.syringeMovement(
            SyrMov.relativeDispense.value, vol)
        cmd += self.psd.command.executeCommandBuffer()
        ham.communication.sendCommand(
            self.psd.asciiAddress, cmd, waitForPump=waitForPump)
        self.target_volume -= vol

    def pickup(self, vol, velocity=None, waitForPump=False):
        if velocity is not None:
            cmd = self.psd.command.setMaximumVelocity(velocity)
        else:
            cmd = ''
        cmd += self.psd.command.syringeMovement(
            SyrMov.relativePickup.value, vol)
        cmd += self.psd.command.executeCommandBuffer()
        ham.communication.sendCommand(
            self.psd.asciiAddress, cmd, waitForPump=waitForPump)
        self.target_volume += vol

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
        Using pyHam functionality for now, therefore no timeout
        Args:
            timeout : float
                timeout in s
        """
        ham.communication.sendCommand(
            self.psd.asciiAddress, 'Q', waitForPump=True)
        # also set volumes
        current_volume = self.get_current_volume()
        self.target_volume = current_volume
        # tic = time.time()
        # while time.time() < tic + timeout:
        #     time.sleep(.05)

    def get_status(self):
        """polls and returns the status
        """
        return ham.communication.sendCommand(
            self.psd.asciiAddress, 'Q', waitForPump=False)

    def get_current_volume(self):
        """Polls the syringe volume position
        """
        response = ham.communication.sendCommand(
            self.psd.asciiAddress,
            self.psd.command.absoluteSyringePosition(),
            waitForPump=True)
        pos_steps = int(self.decode_response(response))
        current_volume = (
            pos_steps / self.psd.command.steps * self.syringe_volume)
        return current_volume

    def stop_current_move(self):
        """Stops the current movement. The pump needs to be
        reinitialized after this (I thought after reading
        the manual. But it works without).
        """
        ham.communication.sendCommand(
            self.psd.asciiAddress,
            self.psd.command.terminateCommandBuffer(),
            waitForPump=False)

    def resume_current_move(self):
        """Resumes the move stopped previously
        """
        current_volume = self.get_current_volume()
        missing_volume = self.target_volume - current_volume
        if missing_volume > 0:
            self.pickup(missing_volume)
        elif missing_volume < 0:
            self.dispense(abs(missing_volume))
        else:
            pass

    def decode_response(self, response):
        """This should go into PyHamiltonPSD, but as they don't provide
        it, let's keep it here for now.
        A PSD reponse is built up as follows
        "/[ADDR][StatusByte][Response][ETX]"
        ADDR: PSD address (self.psd.asciiAddress)
        StatusByte: see pyHamiltonPSD.communciation.statusBytesInfo
            '@': Busy
            '`': ready
        ETX: End of transmission; have to check the ascii
        """
        etx = '\x03'
        if '`' not in response:
            raise ValueError('Not ready yet')
        if etx not in response:
            raise ValueError('Message not complete')
        result = response[response.index('`') + 1:response.index(etx)]
        return result
