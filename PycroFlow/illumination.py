"""
illumination.py

Provides illumination functionality to be used as a system
in orchestration.

illumination protocol e.g.
protocol_illumination = [
    {'$type': 'power', 'value': 1},
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'power', 'value': 50},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
]
"""
from PycroFlow.orchestration import AbstractSystem
import PycroFlow.monet as monet
import PycroFlow.monet.control as mco
import logging
import pprint
import time


logger = logging.getLogger(__name__)

class IlluminationSystem(AbstractSystem):
    def __init__(self):
        """
        """
        pass

    def set_laser(self, laser):
        """set current laser and activate
        Args:
            laser : int
                the laser line to activate
        """
        try:
            logger.debug('Setting laser {:s}.'.format(str(laser)))
            self.instrument.laser = laser
            self.instrument.attenuator.set_wavelength(laser)

            # set laser power back to the value for that laser
            try:
                self.do_power(self.power_setvalues[
                    self.instrument.curr_laser])
            except Exception:
                pass

            # activate
            self.set_laser_enabled(laser, True)
        except ValueError as e:
            print(str(e))

    def set_laser_enabled(self, laser, enabled=True):
        """Set the activation state of a laser in the system
        """
        self.instrument.lasers[laser].enabled = enabled

    def set_laser_power(self, power):
        try:
            logger.debug('Setting laser power to ', int(power))
            self.instrument.laserpower = int(power)
        except ValueError as e:
            print(str(e))

    def set_sample_power(self, power, warmup_delay=0):
        if int(power) != self.instrument.power:
            logger.debug(f'Changing sample power from {self.instrument.power} to {int(power)}')
            try:
                self.instrument.power = int(power)
                self.power_setvalues[self.instrument.curr_laser] = int(power)
            except ValueError as e:
                print(str(e))
            time.sleep(warmup_delay)
        else:
            logger.debug(f'Sample power remains at {int(power)}')

    def set_attenuation(self, pos):
        """Set the attenuation device to a position (float)"""
        if pos.upper() == 'HOME':
            self.instrument.attenuator.home()
        else:
            pos = float(pos)
            self.instrument.attenuator.set(pos)

    def beampath_open(self):
        """open shutter and set the correct light path positions"""
        try:
            self.instrument.beampath.positions = self.mprotocol[
                'beampath'][self.instrument.curr_laser]
        except Exception as e:
            print(str(e))
            return

    def beampath_close(self):
        """close shutter"""
        try:
            self.instrument.beampath.positions = self.mprotocol[
                'beampath']['end']
        except Exception as e:
            print(str(e))
            return

    def log_status(self):
        """Display the status of all available laser lines"""
        for lsr in self.instrument.laser:
            if self.instrument.lasers[lsr].enabled:
                enbl = 'on'
            else:
                enbl = 'off'
            pwr = self.power_setvalues[lsr]
            logger.debug(f"Laser {lsr} is {enbl} and set to {pwr} mW.")
        logger.debug(f'Currently active laser: {self.instrument.curr_laser}')
        try:
            logger.debug(
                f'Current attenuator position: {self.instrument.attenuator.curr_pos()}')
        except Exception:
            pass
        try:
            logger.debug(f'Beam path positions: {self.instrument.beampath.positions}')
        except Exception:
            pass
        try:
            print(f"Autoshutter: {self.instrument.beampath.objects['shutter'].autoshutter}")
        except Exception:
            pass

    def _assign_protocol(self, protocol):
        self.protocol = protocol
        self.parameters = protocol.get('parameters')

        self._load_monet_control(self.parameters['setup'])

    def _load_monet_control(self, mconfig_name):
        """Load the monet Illumnination laser control, including its configuration

        Args:
            mconfig_name : The name of the monet config to use (e.g. 'Crick')
        """
        try:
            mconfig = monet.CONFIGS[mconfig_name]
        except KeyError as e:
            print('Could not find '
                  + mconfig_name + ' in configurations. Aborting.')
            print('All configurations:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(monet.CONFIGS)
            raise e

        try:
            mprotocol = monet.PROTOCOLS[mconfig_name]
        except KeyError:
            print('Could not find '
                  + mconfig_name + ' in protocols. Not using laser control.')
            print('All protocols:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(monet.PROTOCOLS)
            mprotocol = None
        self.mprotocol = mprotocol

        if not hasattr(self, 'instrument'):
            self.instrument = mco.IlluminationLaserControl(
                mconfig, auto_enable_lasers=False)
            self.instrument.beampath.objects['shutter'].autoshutter = True
        try:
            self.instrument.load_calibration_database()
        except Exception:
            raise KeyError(
                'Microscope illumination probably not calibrated yet. '
                + 'Monet Set only works with an existing calibration.')

        # set the power set values
        self.power_setvalues = {}
        for las in self.instrument.laser:
            self.set_laser(las)
            self.power_setvalues[las] = round(self.instrument.power)

        # # switch on autoshutter (is switched on in initialization because
        # # that is necessary for calibrate and adjust)
        # try:
        #     self.instrument.beampath.objects['shutter'].autoshutter = True
        # except Exception:
        #     pass

    def execute_protocol_entry(self, i):
        """execute protocol entry i
        """
        pentry = self.protocol['protocol_entries'][i]
        if pentry['$type'] == 'set power':
            logger.debug(
                'executing protocol entry {:d}: {:s}'.format(i, str(pentry)))

            self.set_laser(pentry['laser'])
            self.set_sample_power(pentry['power'])
            self.beampath_open()

            logger.debug('done executing protocol entry {:d}'.format(i))
        elif pentry['$type'] == 'set shutter':
            logger.debug(
                'executing protocol entry {:d}: {:s}'.format(i, str(pentry)))
            if pentry['state']:
                self.beampath_open()
            else:
                self.beampath_close
        elif pentry['$type'] == 'laser enable':
            if isinstance(pentry['laser'], int):
                self.set_laser_enabled(pentry['laser'], pentry['state'])
            elif isinstance(pentry['laser'], str) and pentry['laser'].lower() == 'all':
                for laser in self.instrument.lasers:
                    self.set_laser_enabled(laser, pentry['state'])

    def pause_execution(self):
        """Pause protocol execution
        """
        pass

    def resume_execution(self):
        """Resume protocol execution after pausing
        """
        pass

    def abort_execution(self):
        """Abort protocol execution
        """
        pass
