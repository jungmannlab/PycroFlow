#!/usr/bin/env python
"""
    PycroFlow/AriaProtocol.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Creates protocols for Fluigent Aria. Re-engineered from Aria-saved
    protocols.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import os
import yaml
from datetime import date

def create_protocol(config, base_name):
    """Create a protocol based on a configuration file.

    Args:
        config : dict
            the 'aria_parameters' part of the flow acquisition configuration
    Returns:
        fname = filename of saved protocol
    """
    basic_protocol = {
        "UserComment": 'null',
    }
    protocol_addition = {
        "InjectionMethod": 0,
        "ZeroPressureBeforeSwitch": 'false',
        "DiffusionLeadVolume": "0 µl",
        "DiffusionLagVolume": "0 µl",
        "DiffusionBufferVolume": "0 µl",
        "BufferReservoir": 9,
        "StartTime": "2022-10-21T17:07:47.6951287+02:00",
        "StartAsap": 'true',
        "PrefillStep": {
            "PrefillEnabled": 'false',
            "WarningMessage": "",
            "DisplayWarning": 'false',
            "WarningType": 0
        }
    }

    steps, reservoir_vols = create_steps(config)
    basic_protocol["Steps"] = steps

    reservoirs = create_reservoirs(config['reservoir_names'], reservoir_vols)
    basic_protocol['Reservoirs'] = reservoirs

    for k, v in protocol_addition.items():
        basic_protocol[k] = v

    # save protocol
    fname = base_name + date.today().strftime('_%y-%m-%d') + '.aseq'
    filename = os.path.join(config['protocol_folder'], fname)

    # with open(filename, 'w') as f:
    #     yaml.dump(basic_protocol, f, default_flow_style=True, canonical=True, default_style='"')
    write_to_file(filename, basic_protocol)
    return fname


def create_reservoirs(reservoir_names, reservoir_vols):
    """Creates the reservoir configuration

    Returns:
        reservoir_names : list of dict
            the description of the reservoirs
    """
    reservoirs = []
    for reservoirnr, name in reservoir_names.items():
        reservoirs.append(
            create_reservoir(reservoirnr-1, name, reservoir_vols.get(name, 0)))
    return reservoirs


def create_reservoir(idx, name, vol):
    """Creates the description of one resrevoir.

    Returns:
        reservoir : dict
    """
    if idx < 8:
        size = 1
    else:
        size = 2
    reservoir = {
        "Reservoir": idx,  # index (starting at 0)
        "Name": name,
        "Volume": "{:d} µl".format(vol),
        "Size": size,  # 1: the 8 in front; 2: the 2 on the side
        "IsOverCapacity": 'false'
    }
    return reservoir

def create_steps(config):
    """Creates the protocol steps one after another

    Returns:
        steps : list of dict
            the aria steps.
        reservoir_vols : dict
            keys: reservoir names, values: volumes
    """
    if config['experiment']['type'] == 'Exchange':
        experiment = config['experiment']
        reservoirs = config['reservoir_names']
        imager_vol = config['vol_imager']
        wash_vol = config['vol_wash']
        steps, reservoir_vols = create_steps_Exchange(
            experiment, reservoirs, imager_vol, wash_vol)
    elif config['type'] == 'MERPAINT':
        steps, reservoir_vols = create_steps_MERPAINT(config)
    else:
        raise KeyError(
            'Experiment type {:s} not implemented.'.format(config['type']))
    return steps, reservoir_vols


def create_steps_Exchange(experiment, reservoirs, imager_vol, wash_vol):
    """Creates the protocol steps for an Exchange-PAINT experiment
    Args:
        experiment : dict
            the experiment configuration
        reservoirs : dict
            keys: 1-10, values: the names of the reservoirs
    Returns:
        steps : list of dict
            the aria steps.
        reservoir_vols : dict
            keys: reservoir names, values: volumes
    """
    # check that all mentioned sources acqually exist
    assert experiment['wash_buffer'] in reservoirs.values()
    assert all([name in reservoirs.values() for name in experiment['imagers']])

    washbuf = experiment['wash_buffer']
    res_idcs = {name: nr-1 for nr, name in reservoirs.items()}
    speed = 80.0  # maximum speed with Flow Sensor S

    steps = []

    step_idx = 1
    steps.append(create_step_inject(
            step_idx, 10, speed, res_idcs[washbuf], TTL_at_end=True))
    reservoir_vols = {washbuf: 10}
    step_idx = 2
    steps.append(create_step_waitforTTL(step_idx))
    for round, imager in enumerate(experiment['imagers']):
        step_idx += 1
        steps.append(create_step_inject(
            step_idx, imager_vol, speed, res_idcs[imager], TTL_at_end=True))
        reservoir_vols[imager] = reservoir_vols.get(imager, 0) + imager_vol

        step_idx += 1
        steps.append(create_step_waitforTTL(step_idx))

        step_idx += 1
        steps.append(create_step_inject(
            step_idx, wash_vol, speed, res_idcs[washbuf], TTL_at_end=False))
        reservoir_vols[washbuf] = reservoir_vols.get(washbuf, 0) + wash_vol

    return steps, reservoir_vols

def create_steps_MERPAINT(config):
    """Creates the protocol steps for an MERPAINT experiment

    Returns:
        steps : list of dict
            the aria steps.
    """
    pass


def create_step_waitforTTL(step_idx):
    """Creates a step to wait for a TTL pulse.

    Returns:
        step : dict
            the step configuration
    """
    step = {
        "$type": "WaitForExternal",
        "Description": "Wait for external signal before proceeding",
        "Timeout": "12:00:00",
        "Index": step_idx,
        "StepNumber": step_idx,
        "TtlStart": 'false',
        "TtlEnd": 'false'
        }
    return step

def create_step_inject(
        step_idx, volume, speed, reservoir_idx, TTL_at_end):
    """Creates a step to wait for a TTL pulse.
    Args:
        step_idx : int
            the index of the step, starting at 0
        volume : int
            volume to inject in integer µl
        speed : int
            injection speed in µl/min
        reservoir_idx : int
            the reservoir to use
        TTL_at_end : bool
            whether or not to send a TTL pulse at the end of the execution.
    Returns:
        step : dict
            the step configuration
    """
    step = {
        "$type": "InjectVolume",
        "Volume": "{:d} µl".format(volume),
        "Description": (
            "Inject {:d} µl".format(volume) +
            " from Reservoir {:d}".format(reservoir_idx+1) +
            " into Chip2 at {:.0f} µl/min".format(speed)),  # here it says Chip2, in the Aria GUI it says Chip1
        "DefaultQ": speed,
        "Reservoir": reservoir_idx,
        "Qorder": "{:.0f} µl/min".format(speed),
        "StringInjectionDestinations": "1, ",  # we only have the one chip
        "Index": step_idx,
        "StepNumber": 0,  # for whatever reason, this is always 0 for injection
        "TtlStart": 'false',
        }
    if TTL_at_end:
        step['Description'] = step['Description'] + ' (TTL)'
        step['TtlEnd'] = 'true'
    else:
        step['TtlEnd'] = 'false'
    return step

def write_to_file(fname, d):
    """write the protocol to file.

    Args:
        fname : str
            the file to write
        d : dict
            the protocol
    """
    with open(fname, 'wb') as f:
        write_dict(f, d, islast=True)

def write_dict(fh, d, indent_lvl=0, key='', islast=False):
    """Start a dict
    Args:
        fh : file handle
    """
    indents = ' '*2*indent_lvl
    if key == '':
        writeline(fh, indents+'{\n')
    else:
        key = '"' + key + '"'
        writeline(fh, indents+str(key)+': {\n')
    indent_lvl += 1
    indents = ' '*2*indent_lvl
    N = len(d.keys())
    for i, (k, v) in enumerate(d.items()):
        if i == N-1:
            sub_islast = True
        else:
            sub_islast = False
        if isinstance(v, dict):
            write_dict(fh, v, indent_lvl, k, islast=sub_islast)
        elif isinstance(v, list):
            write_list(fh, v, indent_lvl, k, islast=sub_islast)
        else:
            if isinstance(v, str):
                if v not in ['true', 'false', 'null']:
                    v = '"' + v + '"'
            elif isinstance(v, int):
                v = str(v)
            elif isinstance(v, float):
                v = '{:.1f}'.format(v)
            else:
                raise NotImplmentedError()
            if not sub_islast:
                v += ','
            k = '"' + k + '"'
            writeline(fh, indents+str(k)+': '+v+'\n')
    indent_lvl -=1
    indents = ' '*2*indent_lvl
    if islast:
        writeline(fh, indents+'}\n')
    else:
        writeline(fh, indents+'},\n')

def write_list(fh, l, indent_lvl=0, key='', islast=False):
    indents = ' '*2*indent_lvl
    if key == '':
        writeline(fh, indents+'[\n')
    else:
        key = '"' + key + '"'
        writeline(fh, indents+str(key)+': [\n')
    indent_lvl += 1
    indents = ' '*2*indent_lvl
    N = len(l)
    for i, v in enumerate(l):
        if i == N-1:
            sub_islast = True
        else:
            sub_islast = False
        if isinstance(v, dict):
            write_dict(fh, v, indent_lvl, islast=sub_islast)
        elif isinstance(v, list):
            write_list(fh, v, indent_lvl, islast=sub_islast)
        else:
            if isinstance(v, str):
                if v not in ['true', 'false', 'null']:
                    v = '"' + v + '"'
            elif isinstance(v, int):
                v = str(v)
            elif isinstance(v, float):
                v = '{:.1f}'.format(v)
            else:
                raise NotImplmentedError()
            if not sub_islast:
                v += ','
            writeline(fh, indents+v+'\n')
    indent_lvl -=1
    indents = ' '*2*indent_lvl
    if islast:
        writeline(fh, indents+']\n')
    else:
        writeline(fh, indents+'],\n')

def writeline(fh, line):
    fh.write(line.encode('utf8'))

def write_entry(fh, e, indent_lvl=0):
    pass
