"""
address are unique to the whole system (same as serial address)


"""
import PyHamiltonPSD as ham
import numpy as np


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
        {'address': 0, 'intrument_type': 'MVP', 'valve_type': '8-way'},
        {'address': 1, 'intrument_type': 'MVP', 'valve_type': '8-way'},
        ],
    'pump_a': {'address': 2, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '500µl'},
    'pump_out': {'address': 3, 'instrument_type': '4', 'valve_type': 'Y', 'syringe': '500µl'},
    'reservoir_a': [
        {'id': 0, 'valve_pos': {0: 3, 1: 2}},
        {'id': 1, 'valve_pos': {0: 2, 1: 2}},
        ],
    'flushbuffer_a': 0,  # defines the reservoir id with the buffer that can be used for flushing
}

# description of tubing volumes between
# reservoirs -> confluxes(if present) -> pumps
# connections are set fixed for each system architecture
legacy_tubing_config = {
    (0, 'pump_a'): 153.2,
    (1, 'pump_a'): 151.8,
    ('pump_a', 'sample'): 30.7,
}


class Valve(ham.Valve):
    def __init__(address, instrument_type, valve_type):
        self.__super__.__init__()

class Reservoir():
    def __init__(id, valve_positions):
        pass

class Pump(ham.Pump):
    def __init__(address, instrument_type, valve_type, syringe):
        pass


class LegacyArchitecture():
    """
    """
    valve_a = {}
    pump_a = None
    pump_out = None
    tubing_config = {}
    reservoir_a = []
    protocol = []
    last_protocol_entry = -1

    def __init__(self):
        pass

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
        self.pump_a = Pump(config['pump_a'])
        self.pump_out = Pump(config['pump_out'])

    def _assign_protocol(self, protocol):
        self.protocol = protocol
        raise NotImplmentedError('There is more to be done here')

    def _assign_tubing_config(self, config):
        self.tubing_config = config
        raise NotImplmentedError('There is more to be done here')

    def perform_next_protocol_entry(self):
        """Performs the next entry in the dispension protocol
        """
        curr_protocol_entry = self.last_protocol_entry + 1
        for reservoir_id, vol in self.tubing_column[curr_protocol_entry]:
            self._set_valves(reservoir_id)
            self._inject(vol)
        self.last_protocol_entry = curr_protocol_entry

    def jump_to_protocol_entry(self, i):
        """When not executing the next protocol entry but jumpint to one
        out of order, the tubing-'column' needs to be re-assembled.

        A protocol entry consists of:
            type, parameters.
        e.g.
            type='inject', reserviorID, volume, speed, extractionfactor
            type='wait_image',
            type='wait_time', duration
        """
        self._assemble_tubing_column(i)
        for reservoir_id, vol in self.tubing_column[i]:
            self._set_valves(reservoir_id)
            self._inject(vol)
        self.last_protocol_entry = i

    def execute_single_protocol_entry(self, i):
        """Execute only one single entry of the protocol; do not fill the tubing
        with the (potentially precious) later protocol entry fluids, but with
        buffer.
        """
        pentry = self.protocol[i]
        if pentry['type'] == 'inject':
            flush_volume = self._calc_vol_to_inlet(pentry['reservoir_id'])
            injection_volume = pentry['volume']
            # first, set up the volume required
            self._set_valves(pentry['reservoir_id'])
            self._inject(injection_volume, pentry['speed'], extractionfactor)
            # afterwards, flush in buffer to get the pentry volume to the sample 
            self._set_valves(self.config['flushbuffer_a'])
            self._inject(flush_volume, pentry['speed'], extractionfactor)
        else:
            raise NotImplmentedError('There is more to be done here')
        self.last_protocol_entry = -1  # tubing full of buffer, cannot simply proceed

    def _assemble_tubing_column(self, i):
        """Assemble the 'column' of different fluids stacked into the tubing.
        In an efficient delivery, when delivering fluid of step i into the
        sample, the tubing already needs to be switched to fluids of later steps.

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
            reservoirs[idx] = pentry['reservoir_id']
            volumes = pentry['volume']
        reservoirs[-1] = self.config['flushbuffer_a']
        volumes[-1] = self._calc_vol_to_inlet(reservoirs[-1])

        volumes_cum = np.cumsum(volumes)

        for idx, step in enumerate(range(start=i, stop=i + nsteps)):
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
            cum_stop = np.arghwere(volumes_cum < vol).flatten()[0]
            for cum_idx in range(cum_start, cum_stop):
                vol_step = min([vol_rest, volumes_cum[cum_idx]])
                injection_tuples.append(tuple(
                    reservoirs[cum_idx],
                    vol_step))
                volumes_cum -= vol_step
                vol_rest -= vol_step
            column[step] = injection_tuples
        self.tubing_column = column

    def _calc_vol_to_inlet(self, reservoir_id):
        """Calculates the tubing volume between reservoir and inlet needle
        from the tubing configuration. This is legacy system specific
        """
        vol_res_pump_a = self.tubing_config[(reservoir_id, 'pump_a')]
        vol_pump_a_inlet = self.tubing_config[('pump_a', 'sample')]
        return vol_res_pump_a + vol_pump_a_inlet

    def _set_valves(self, reservoir_id):
        """Set the valves to access the reservoir specified
        """
        valve_positions = self.reservoir_a[reservoir_id].get_valve_positions()
        for valve, pos in valve_positions.items():
            self.valve_a[valve].set(pos)

    def _inject(self, vol, speed=None, extractionfactor=None):
        """Inject volume from the currently selected reservoir into
        the sample with a given flow speed. Simultaneously, the extraction
        pump extracts the same volume.

        Args:
            vol : float
                the volume to inject in µl
            speed : float
                the flow speed of the injection in µl/min
            extractionfactor : float
                the factor of flow speeds of extraction vs injection.
                a non-perfectly calibrated system may result in less volume
                being extracted than injected, leading to spillage. To prevent
                this, the extraction needle could be positioned higher, and
                extraction performed faster than injection
        """
        if speed is None:
            speed = self.protocol['speed']
        if extractionfactor is None:
            extractionfactor = self.protocol['extractionfactor']
        if self.pump_a.curr_vol > 0:
            self.pump_a.set_valve('out')
            self.pump_out.set_valve('in')
            self.pump_a.eject(self.pump_a.curr_vol, speed)
            self.pump_out.suck(
                self.pump_a.curr_vol * extractionfactor,
                speed * extractionfactor)
            self.pump_a.wait_till_done()
            self.pump_out.wait_till_done()

        volume_quant = max([
            self.pump_a.syringe_volume,
            self.pump_out.syringe_volume / extractionfactor])
        nr_pumpings = vol // volume_quant
        pump_volumes = volume_quant * np.ones(nr_pumpings + 1)
        pump_volumes[-1] = vol % volume_quant

        for pump_volume in pump_volumes:
            self.pump_a.set_valve('in')
            self.pump_out.set_valve('out')
            self.pump_a.suck(pump_volume, speed)
            self.pump_out.eject(
                self.pump_a.curr_vol * extractionfactor,
                speed * extractionfactor)
            self.pump_a.wait_till_done()
            self.pump_out.wait_till_done()

            self.pump_a.set_valve('out')
            self.pump_out.set_valve('in')
            self.pump_a.eject(pump_volume, speed)
            self.pump_out.suck(
                pump_volume * extractionfactor,
                speed * extractionfactor)
            self.pump_a.wait_till_done()
            self.pump_out.wait_till_done()

        self.pump_out.set_valve('out')
        self.pump_out.eject(
            self.pump_a.curr_vol * extractionfactor,
            speed * extractionfactor)
        self.pump_out.wait_till_done()


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