from PycroFlow.frontend_cli import PycroFlowInteractive
from PycroFlow.protocols import ProtocolBuilder
import yaml


hamilton_config = {
    'interface': {
        'COM': '18',
        'baud': 9600},
    'system_type': 'legacy',
    'valve_a': [
        {'address': 2, 'instrument_type': 'MVP', 'valve_type': '8-5'},
        {'address': 3, 'instrument_type': 'MVP', 'valve_type': '8-5'},
        {'address': 4, 'instrument_type': 'MVP', 'valve_type': '8-5'}],
    'valve_flush':
        {'address': 5, 'instrument_type': 'MVP', 'valve_type': '4-2'},
    'flush_pos': {'inject': 4, 'flush': 1}, # 1: flush/waste/pumptoblue; 2: pump sealed; 3: pump sealed; 4: inject/sample/pumptored
    'pump_a':
        {'address': 1, 'instrument_type': '4', 'valve_type': 'Y',
         'syringe': '500u'},
    'pump_out': {
        'address': 0, 'instrument_type': '4', 'valve_type': 'Y',
        'syringe': '5.0m'},
    'reservoir_a': [
        {'id': 0, 'valve_pos': {4: 2}},
        {'id': 1, 'valve_pos': {4: 3}},
        # {'id': 2, 'valve_pos': {4: 4}},
        # {'id': 3, 'valve_pos': {4: 5}},
        # {'id': 4, 'valve_pos': {4: 6}},
        # {'id': 5, 'valve_pos': {4: 7}},
        # {'id': 6, 'valve_pos': {4: 8}},
        {'id': 7, 'valve_pos': {4: 1, 3: 2}},
        {'id': 8, 'valve_pos': {4: 1, 3: 3}},
        # {'id': 9, 'valve_pos': {4: 1, 3: 4}},
        # {'id': 10, 'valve_pos': {4: 1, 3: 5}},
        # {'id': 11, 'valve_pos': {4: 1, 3: 6}},
        # {'id': 12, 'valve_pos': {4: 1, 3: 7}},
        # {'id': 13, 'valve_pos': {4: 1, 3: 8}},
        {'id': 14, 'valve_pos': {4: 1, 3: 1, 2: 1}},
        {'id': 15, 'valve_pos': {4: 1, 3: 1, 2: 2}},
        {'id': 16, 'valve_pos': {4: 1, 3: 1, 2: 3}},
        # {'id': 17, 'valve_pos': {4: 1, 3: 1, 2: 4}},
        # {'id': 18, 'valve_pos': {4: 1, 3: 1, 2: 5}},
        # {'id': 19, 'valve_pos': {4: 1, 3: 1, 2: 6}},
        # {'id': 20, 'valve_pos': {4: 1, 3: 1, 2: 7}},
        # {'id': 21, 'valve_pos': {4: 1, 3: 1, 2: 8}},
        ],
    'special_names': {
        'flushbuffer_a': 14,  # defines the reservoir id with the buffer that can be used for flushing should be at the end of multiple MVPs
        },
}


tubing_config = {
    ('R21', 'pump_a'): 365,
    ('R20', 'pump_a'): 365,
    ('R19', 'pump_a'): 365,
    ('R18', 'pump_a'): 365,
    ('R17', 'pump_a'): 365,
    ('R16', 'pump_a'): 365,
    ('R15', 'pump_a'): 365,
    ('R14', 'pump_a'): 365,
    ('R13', 'pump_a'): 260,
    ('R12', 'pump_a'): 260,
    ('R11', 'pump_a'): 260,
    ('R10', 'pump_a'): 260,
    ('R9', 'pump_a'): 260,
    ('R8', 'pump_a'): 260,
    ('R7', 'pump_a'): 260,
    ('R6', 'pump_a'): 215,
    ('R5', 'pump_a'): 215,
    ('R4', 'pump_a'): 215,
    ('R3', 'pump_a'): 215,
    ('R2', 'pump_a'): 215,
    ('R1', 'pump_a'): 215,
    ('R0', 'pump_a'): 215,
    ('pump_a', 'valve_flush'): 156,
    ('valve_flush', 'sample'): 256,
}


imaging_config = {
    'save_dir': r'.',
    'base_name': 'AutomationTest',
}


fluid = {
    'parameters': {
        'start_velocity': 50,
        'max_velocity': 1000,
        'stop_velocity': 500,
        'mode': 'tubing_stack',  # or 'tubing_flush'
        'extractionfactor': 1},
    'settings': {
        'vol_wash': 500,  # in ul
        'vol_imager_pre': 500,  # in ul
        'vol_imager_post': 100,  # in ul
        'reservoir_names': {
            1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
            7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
        'experiment' : {
            'type': 'Exchange',  # options: ['Exchange', 'MERPAINT', 'FlushTest']
            'wash_buffer': 'Buffer B+',
            'imagers': [
                'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2'],}
    }
}


imaging = {
    'settings': {
        'frames': 50000,
        't_exp': 100,  # in ms
        }
}
illumination = {
    'parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
        'ROI': [512, 512, 512, 512]},
    'settings': {
        'setup': 'Mercury',
        'laser': 560,
        'power': 30,  #mW
        }
}

flow_acq_config = {
    'save_dir': r'.',
    'base_name': 'AutomationTest',
    'fluid': fluid,
    'img': imaging,
    # 'illu': illumination,
}


if __name__ == '__main__':
    pb = ProtocolBuilder()
    protocol_fname, _ = pb.create_protocol(flow_acq_config)

    pfi = PycroFlowInteractive()
    pfi.do_load_hamilton(hamilton_config, tubing_config)
    pfi.do_load_imaging(imaging_config)
    pfi.do_load_protocol(protocol_fname)
    pfi.cmdloop()