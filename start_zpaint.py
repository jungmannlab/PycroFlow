#!/usr/bin/env python
"""
    PycroFlow/start_zpaint.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    start a DNA-PAINT zstack: from micromanager, read the multi-d-acquisition
    settings, convert the relative z positions to PSF offset values, and
    perform separate acquisitions

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import os
import time
import yaml
import numpy as np
from pycromanager import Studio, Core, Acquisition, multi_d_acquisition_events

TAG_PFS = 'PFSOffset'


def zpos2pfsoffset(zpos, curr_offset, calibration=None):
    """convert z position values to PFS offset values.
    Args:
        zpos : np array, 1d
            the z positions relative to the current position
        curr_offset : float
            the current PFS offset value
        calibration : array (N, 2)
            the calibration from stage values (1st col) to PFS offset values
            (2nd col)
    Returns:
        pfs_values : array, same shape as zpos
            the PFS offest values corresponding to the zpos values
    """
    if calibration is not None:
        return zpos
    else:
        curr_zpos = np.interp(
            curr_offset, calibration[:, 1], calibration[:, 0])
        z_relative = calibration[:, 0] - curr_zpos
        pfs_values = np.interp(
            zpos, z_relative, calibration[:, 1])
        return pfs_values


def get_multid_settings(studio):
    """Get the multi-d settings in micromanager
    Args:
        studio : MMStudio
            the current micromanager studio instance
    Returns:
        acq : dict
            the acquisition description, with keys
            'usedims', 'Z', 'C', 'S', 'comment', 'prefix', 'root'
    """
    #studio = Studio(convert_camel_case=True)
    acqmgr = studio.get_acquisition_manager()
    acqsttgs = acqmgr.get_acquisition_settings()

    # https://valelab4.ucsf.edu/~MM/doc-2.0.0-gamma/mmstudio/org/micromanager/acquisition/SequenceSettings.html
    usedims = {}
    usedims['C'] = acqsttgs.use_channels()
    usedims['T'] = acqsttgs.use_frames()
    usedims['S'] = acqsttgs.use_position_list()
    usedims['Z'] = acqsttgs.use_slices()

    channels = [acqsttgs.channels().get(i)
                for i in range(acqsttgs.channels().size())]

    z_definition = {
        'slices': [acqsttgs.slices().get(i)
                   for i in range(acqsttgs.slices().size())],
        'bot': acqsttgs.slice_z_bottom_um(),
        'step': acqsttgs.slice_z_step_um(),
        'top': acqsttgs.slice_z_top_um(),
        'relative': acqsttgs.relative_z_slice()
    }

    t_definition = {
        'n': acqsttgs.num_frames(),
        'dt': acqsttgs.interval_ms(),
    }

    # https://forum.image.sc/t/importing-coordinates-from-multi-d-acquisition-stage-position-list-through-pycromanager/46746
    # positions = (
    #     studio.get_position_list_manager().get_position_list().get_positions())
    pos_list = studio.get_position_list_manager().get_position_list()
    positions = [
        pos_list.get_position(i)
        for i in range(pos_list.get_number_of_positions())]

    comment = acqsttgs.comment()

    acq = {
        'usedims': usedims,
        'Z': z_definition,
        'C': channels,
        'S': positions,
        'T': t_definition,
        'comment': comment,
        'prefix': acqsttgs.prefix(),
        'root': acqsttgs.root()
    }
    return acq


def mm2zpaint_acq(mmacq, PFScalibration):
    """Transform the micromanager acquisition settings to zPAINT
    acquisition settings (perforoming separate multi-d acquisitions)
    Args:
        mmacq : dict
            the micromanager acquisition settings, retrieved by
            get_multid_settings
        PFScalibration : array, shape (N, 2)
            the z-PFS calibration. 1st col: z vals, 2nd col: PFS vals
    Returns:
        multid_sttg : dict
            the multi-d acquisition settings (only T)
        zpacq : dict
            the reimaining dimensions, to be taken care from pycromanager
    """
    multid_sttg = {
        'usedims': {'C': False, 'T': mmacq['usedims']['T'], 'Z': False, 'S': False},
        'T': mmacq['T'],
        'C': [],
        'S': [],
        'Z': [],
        'comment': mmacq['comment'],
        'prefix': mmacq['prefix'],
        'root': mmacq['root'],
    }

    zpacq = {
        'usedims': {
            'C': mmacq['usedims']['C'], 'T': False, 'Z': mmacq['usedims']['Z'], 'S': mmacq['usedims']['S']},
        'T': [],
        'C': mmacq['C'],
        'S': mmacq['S'],
        'Z': map_z_sttg(mmacq['Z'], PFScalibration),
        'comment': mmacq['comment'],
        'prefix': mmacq['prefix'],
        'root': mmacq['root'],
    }
    return multid_sttg, zpacq


def map_z_sttg(z_sttg, PFScalibration):
    """Map z-settings from micromanager (in um) to PFS-offset settings
    Args:
        z_sttg : dict
            the z-settings as retrieved in get_multid_settings, with keys:
            'slices', 'bot', 'top', 'step', 'relative'
        PFScalibration : array, shape (N, 2)
            the z-PFS calibration. 1st col: z vals, 2nd col: PFS vals
    Returns:
        z_sttg_pfs : dict
            the mapped settings, with keys
            'slices', 'bot', 'top'
    """
    try:
        assert z_sttg['relative']
    except:
        raise NotImplmentedError(
            'zPAINT experiments with absolute z settings are not implemented')

    curr_pfsoffset = core_get_pfsoffset()
    z_sttg_pfs = {
        'slices': zpos2pfsoffset(
            z_sttg['slices'], curr_pfsoffset, PFScalibration),
        'top': zpos2pfsoffset(z_sttg['top'], curr_pfsoffset, PFScalibration),
        'bot': zpos2pfsoffset(z_sttg['bot'], curr_pfsoffset, PFScalibration),
    }
    return z_sttg_pfs


def core_get_pfsoffset():
    global core
    if core is None:
        core = Core()
    return core.get_position(TAG_PFS)


def core_set_pfsoffset(pfs):
    global core
    if core is None:
        core = Core()
    core.set_position(TAG_PFS, pfs)


def start_acq(acq_settings):
    """Starts a zPAINT acquisition, based on the multi-d settings in
    micromanager.

    Args:
        acq_settings : dict
            additional settings for the acquisition
    """
    if acq_settings.get('PFScalibration'):
        PFScalibration = np.load(acq_settings['PFScalibration'])
    else:
        PFScalibration = None

    studio = Studio(convert_camel_case=False)
    acq = get_multid_settings(studio)
    multid_sttg, zpacq = mm2zpaint_acq(acq, PFScalibration)

    # create the root folder
    dirpath = os.path.join(zpacq['root'], zpacq['prefix'])
    ext = ''
    extit = 0
    while os.path.exists(dirpath + ext):
        extit += 1
        ext = '_{:d}'.format(extit)
    dirpath = dirpath + ext
    os.mkdir(dirpath)

    # save settings
    acquisition_config = {
        'inner dimensions': multid_sttg,
        'outer dimensions': zpacq,
        'acquisition_settings': acq_settings,
    }
    with open(os.path.join(
            dirpath, 'acquisition_configuration.yaml'), 'w') as f:
        yaml.dump(acquisition_config, f)

    # create multi-d-acquisition events
    print(multid_sttg)
    events = multi_d_acquisition_events(
        num_time_points=multid_sttg['T']['n'],
        time_interval_s=multid_sttg['T']['dt'] / 1000)

    # fix dimension order to SCZT
    for i_s, pos in zp_iterate_S(zpacq):
        for i_c, c in zp_iterate_C(zpacq):
            for i_z, z in zp_iterate_Z(zpacq):
                prefix = zpacq['prefix'] + '_S{:d}_C{:d}_Z{:d}'.format(
                    i_s, i_c, i_z)
                with Acquisition(directory=dirpath, name=prefix) as acq:
                    acq.acquire(events)


def zp_iterate_S(acq):
    """Iterator over the S dimension of a zpaint acquisition
    Args:
        acq : dict
    Yields:
        iteration : int
            the iteration number
        sval : MultiPosition
            the position
    """
    global core
    if core is None:
        core = Core()

    if not acq['usedims']['S'] or acq['S'] == []:
        yield 0, 0
    else:
        for i, pos in enumerate(acq['S']):
            pos.go_to_position(pos, core)
            yield i, pos


def zp_iterate_C(acq):
    """Iterator over the C dimension of a zpaint acquisition
    Args:
        acq : dict
    Yields:
        iteration : int
            the iteration number
        cval : int
            the the channel index
    """
    global core
    if core is None:
        core = Core()

    if not acq['usedims']['C'] or acq['C'] == []:
        yield 0, 0
    else:
        for i, chan in enumerate(acq['C']):
            chan_group = chan.channel_group()
            filt = chan.config()
            core.set_config(chan_group, filt)
            yield i, chan


def zp_iterate_Z(acq):
    """Iterator over the Z dimension of a zpaint acquisition
    Args:
        acq : dict
    Yields:
        iteration : int
            the iteration number
        zval : float
            the PFS offset position
    """
    if not acq['usedims']['Z'] or acq['Z'] == []:
        yield 0, 0
    else:
        for i, pfsoffset in enumerate(acq['Z']['slices']):
            core_set_pfsoffset(pfsoffset)
            yield i, pfsoffset


if __name__ == '__main__':
    global core
    core = Core()

    acq_settings = {
        'PFScalibration': 'PFScalibration_dummy.npy'
    }
    start_acq(acq_settings)
