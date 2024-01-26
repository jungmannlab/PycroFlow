from PycroFlow.frontend_cli import PycroFlowInteractive
from PycroFlow.protocols import ProtocolBuilder
import yaml
import os

################ CHANGE EXPERIMENT SETTINGS HERE ################
'''Set the name of the experiment. This will be the base folder.'''
experiment_name = 'SKBR3_6plex'

# volume settings
wash_volume = 2000  # ul
imager_volume = 800  # ul
volume_reduction_for_xchg = 130  # ul

# fluidics settings
wash_buffer = 'PBS'

'''Set at which tubing number to find which solution.'''
reservoir_names = {
    1: 'EGFR', 2: '5T4', 3: 'cMet', 4: 'AXL', 5: 'B7H3', 6: wash_buffer}

'''If an imager is already present in the sample and ready for imaging
without prior fluid exchange, write its name here. Otherwise, set
to None.'''
initial_target = 'HER3'

'''Set the sequence in which the targets should be imaged. Make sure
that all these names match those in reservoir_names.'''
target_sequence = ['EGFR', '5T4', 'AXL', 'B7H3', 'cMet']

# imaging settings
exposure_time = 75  # ms
n_frames = 15000
use_mm_positions = True

############# NO NEED TO CHANGE ANYTHING BELOW HERE ##############

reservoir_a_connections = [
    {'id': 1, 'valve_pos': {1: 6}},
    {'id': 2, 'valve_pos': {1: 7}},
    {'id': 3, 'valve_pos': {1: 8}},
    {'id': 4, 'valve_pos': {1: 1}},
    {'id': 5, 'valve_pos': {1: 2}},
    {'id': 6, 'valve_pos': {2: 1, 1: 5}},
    {'id': 7, 'valve_pos': {2: 2, 1: 5}},
    {'id': 8, 'valve_pos': {2: 3, 1: 5}},
    {'id': 9, 'valve_pos': {2: 4, 1: 5}},
    {'id': 10, 'valve_pos': {2: 5, 1: 5}},
    {'id': 11, 'valve_pos': {2: 6, 1: 5}},
    {'id': 12, 'valve_pos': {2: 7, 1: 5}},
    {'id': 13, 'valve_pos': {2: 8, 1: 5}},
    ]
    
resa_conn_present = []
for res_used in reservoir_names.keys():
    used_conn = [
        resa_conn for resa_conn in reservoir_a_connections
        if resa_conn['id'] == res_used]
    if len(used_conn) == 1:
        used_conn = used_conn[0]
    else:
        raise KeyError('Used reservoirs must be specified exactly once!')
    resa_conn_present.append(used_conn)

hamilton_config = {
    'interface': {
        'COM': '43',
        'baud': 9600},
    'system_type': 'legacy',
    'valve_a': [
        {'address': 2, 'instrument_type': 'MVP', 'valve_type': '8-5'}],
    'pump_a': {
        'address': 1, 'instrument_type': '4', 'valve_type': '8-5',
        'syringe': '500u', 'input_pos': None, 'output_pos': 4,
        'motorsteps_per_step': 2},  # OEM/high force version internally counts in half-steps (total 6000 steps per stroke), lab version with full steps (3000 steps per stroke)
    'flush_pos': {'flush': 3, 'inject': 4},
    'pump_out': {
        'address': 0, 'instrument_type': '4', 'valve_type': 'Y',
        'syringe': '5.0m', 'input_pos':'out', 'output_pos': 'in',
        'motorsteps_per_step': 2},
    'reservoir_a': resa_conn_present,
    'special_names': {
        'flushbuffer_a': 6,  # defines the reservoir id with the buffer that can be used for flushing should be at the end of multiple MVPs
        'rbs': 11,
        'ipa': 12,
        'h2o': 13,
        'empty': 14
        },
}


tubing_config = {
    ('R13', 'V2'): 20,
    ('R12', 'V2'): 20,
    ('R11', 'V2'): 20,
    ('R10', 'V2'): 20,
    ('R9', 'V2'): 20,
    ('R8', 'V2'): 20,
    ('R7', 'V2'): 20,
    ('R6', 'V2'): 20,
    ('R5', 'pump_a'): 20,
    ('R4', 'pump_a'): 20,
    ('R3', 'pump_a'): 20,
    ('R2', 'pump_a'): 20,
    ('R1', 'pump_a'): 20,
    ('V2', 'pump_a'): 20,
    ('pump_a', 'sample'): 70,
}


imaging_config = {
    'save_dir': r'.',
    'use_positions': use_mm_positions,
}


fluid = {
    'parameters': {
        'start_velocity': 50,
        'max_velocity': 200,
        'stop_velocity': 50,
        'pumpout_dispense_velocity': 300,
        'clean_velocity': 1500,
        'mode': 'tubing_ignore',  # 'tubing_stack' or 'tubing_flush' or 'tubing_ignore'
        'extractionfactor': 4},
    'settings': {
        'vol_wash_pre': int(0.1 * wash_volume),  # in ul
        'vol_wash': int(0.9 * wash_volume),  # in ul
        'vol_imager_pre': int(0.1 * imager_volume),  # in ul
        'vol_imager_post': int(0.1 * imager_volume),  # in ul
        'vol_remove_before_wash': volume_reduction_for_xchg,
        'wait_after_pickup': 5,
        'reservoir_names': reservoir_names,
        'experiment' : {
            'type': 'Exchange',  # options: ['Exchange', 'MERPAINT', 'FlushTest']
            'wash_buffer': wash_buffer,
            'imagers': target_sequence,
            'initial_imager': initial_target}
    }
}


imaging = {
    'parameters': {
        'show_progress': True,
        'show_display': True,
        'close_display_after_acquisition': True,
        },
    'settings': {
        'frames': n_frames,
        'darkframes': 50,
        't_exp': exposure_time,  # in ms
        }
}
illumination = {
    'parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
        'ROI': [512, 512, 512, 512]},
    'settings': {
        'setup': 'Crick',
        'laser': 560,
        'power': 30,  #mW
        }
}

flow_acq_config = {
    'save_dir': r'.',
    'base_name': experiment_name,
    'fluid': fluid,
    'img': imaging,
    # 'illu': illumination,
}


if __name__ == '__main__':
    pb = ProtocolBuilder()
    protocol_fname, _ = pb.create_protocol(flow_acq_config)
    imaging_config['base_name'] = os.path.splitext(protocol_fname)[0]

    pfi = PycroFlowInteractive()
    pfi.do_load_hamilton(hamilton_config, tubing_config)
    pfi.do_load_imaging(imaging_config)
    pfi.do_load_protocol(protocol_fname)
    pfi.cmdloop()