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
import yaml
from pycromanager import Studio, Core, Acquisition, multi_d_acquisition_events

TAG_PFS = 'TIPFSOffset'


def zpos2pfsoffset(zpos, curr_offset, calibration=None):
    """convert z position values to PFS offset values.
    Args:
        zpos : np array, 1d
            the relative z positions
        curr_offset : float
            the current PFS offset value
    """
    if not calibration:
        return zpos
    else:
        raise NotImplmentedError(
            'mapping from zpos to pfsoffset has not been implemented.')


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
    acqmgr = studio.getAcquisitionManager()
    acqsttgs = acqmgr.getAcquisitionSettings()

    # https://valelab4.ucsf.edu/~MM/doc-2.0.0-gamma/mmstudio/org/micromanager/acquisition/SequenceSettings.html
    usedims = {}
    usedims['C'] = acqsttgs.useChannels()
    usedims['T'] = acqsttgs.useFrames()
    usedims['S'] = acqsttgs.usePositionList()
    usedims['Z'] = acqsttgs.useSlices()

    channels = [acqsttgs.channels().get(i)
                for i in range(acqsttgs.channels().size())]

    z_definition = {
        'slices': [acqsttgs.slices().get(i)
                   for i in range(acqsttgs.slices().size())],
        'bot': acqsttgs.sliceZBottomUm(),
        'step': acqsttgs.sliceZStepUm(),
        'top': acqsttgs.sliceZTopUm(),
        'relative': acqsttgs.relativeZSlice()
    }

    t_definition = {
        'n': acqsttgs.numFrames(),
        'dt': acqsttgs.intervalMs(),
    }

    # https://forum.image.sc/t/importing-coordinates-from-multi-d-acquisition-stage-position-list-through-pycromanager/46746
    # positions = (
    #     studio.getPositionListManager().getPositionList().getPositions())
    pos_list = studio.getPositionListManager().getPositionList()
    positions = [
        pos_list.get_position(i)
        for i in range(pos_list.getNumberOfPositions())]

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


def mm2zpaint_acq(mmacq):
    """Transform the micromanager acquisition settings to zPAINT
    acquisition settings (perforoming separate multi-d acquisitions)
    Args:
        mmacq : dict
            the micromanager acquisition settings, retrieved by
            get_multid_settings
    Returns:
        multid_sttg : dict
            the multi-d acquisition settings (only T)
        zpacq : dict
            the reimaining dimensions, to be taken care from pycromanager
    """
    multid_sttg = {
        'usedims': {'C': False, 'T': mmacq['T'], 'Z': False, 'S': False},
        'T': mmacq['T'],
        'C': [],
        'S': [],
        'T': [],
        'comment': mmacq['comment'],
        'prefix': mmacq['prefix'],
        'root': mmacq['root'],
    }

    zpacq = {
        'usedims': {
            'C': mmacq['C'], 'T': False, 'Z': mmacq['Z'], 'S': mmacq['S']},
        'T': [],
        'C': mmacq['C'],
        'S': mmacq['S'],
        'Z': map_z_sttg(mmacq['Z']),
        'comment': mmacq['comment'],
        'prefix': mmacq['prefix'],
        'root': mmacq['root'],
    }
    return multid_sttg, zpacq


def map_z_sttg(z_sttg):
    """Map z-settings from micromanager (in um) to PFS-offset settings
    Args:
        z_sttg : dict
            the z-settings as retrieved in get_multid_settings, with keys:
            'slices', 'bot', 'top', 'step', 'relative'
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
        'slices': zpos2pfsoffset(z_sttg['slices'], curr_pfsoffset),
        'top': zpos2pfsoffset(z_sttg['top'], curr_pfsoffset),
        'bot': zpos2pfsoffset(z_sttg['bot'], curr_pfsoffset),
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


def start_acq():
    studio = Studio(convert_camel_case=False)
    acq = get_multid_settings(studio)
    multid_sttg, zpacq = mm2zpaint_acq(acq)

    # create the root folder
    dirpath = os.path.join(zpacq['root'], zpacq['prefix'])
    ext = ''
    extit = 0
    while os.exists(dirpath + ext):
        extit += 1
        ext = '_{:d}'.format(extit)
    dirpath = dirpath + ext
    os.mkdir(dirpath)

    # save settings
    acquisition_config = {
        'inner dimensions': multid_sttg,
        'outer dimensions': zpacq,
    }
    with open(os.path.join(
            dirpath, 'acquisition_configuration.yaml'), 'w') as f:
        yaml.dump(acquisition_config, f)

    # create multi-d-acquisition events
    events = multi_d_acquisition_events(
        num_time_points=multid_sttg['n'],
        time_interval_s=multid_sttg['dt'] / 1000)

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

    if not acq['usedim']['S'] or acq['S'] == []:
        yield 0, 0
    else:
        for i, pos in enumerate(acq['S']):
            pos.goToPosition(pos, core)
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

    if not acq['usedim']['C'] or acq['C'] == []:
        yield 0, 0
    else:
        for i, chan in enumerate(acq['C']):
            chan_group = chan.channelGroup()
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
    if not acq['usedim']['Z'] or acq['Z'] == []:
        yield 0, 0
    else:
        for i, pfsoffset in enumerate(acq['Z']['slices']):
            core_set_pfsoffset(pfsoffset)
            yield i, pfsoffset


if __name__ == '__main__':
    start_acq()
