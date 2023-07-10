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
from datetime import datetime 
import yaml
import numpy as np
from pycromanager import Studio, Core, Acquisition, multi_d_acquisition_events


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


def move_pfsoffset(
        zpos, core, ztags, jump_factor_settings, max_iter=50,
        tolerance=.01, approx_stepsperum=1000):
    """move the Nikon PFS offset such that the ZDrive-position
    reaches the target given.
    Args:
        zpos : float
            absolute microscope ZDrive position to go to
        core : MM Core instance
            access to the hardware
        ztags : dict
            micromanager configuration tags. Keys:
                'zdrive', 'pfsoffset', 'pfsstate'
        jump_factor_settings : dict
            settings to adjust the jump size. If the distance to zpos
            is within the thresholds, adjust the jump size by the factor
            items:
                thresholds : tuple, len N
                    strictly monotonically increasing. thresholds in microns
                factors : tuple, len N+1
                    strictly monotonically increasing, between 0 and 1
        max_iter : int
            the maximum number of jumps
        tolerance : float
            the error in zpos to accept as target reached in um
        approx_stepsperum : number
            the approximate number of PFS steps per micron
    Returns:
        pfsoffset_pos : np array, shape (max_iter, )
            the pfs offset positions moved to
        zdrive_pos : np array, shape (max_iter, )
            the zdrive positions queried
    """
    zdrive_pos = np.nan * np.ones(max_iter)
    pfsoffset_pos = np.nan * np.ones(max_iter)

    for i in range(max_iter):
        zdrive_pos[i] = core.get_position(ztags['zdrive'])
        pfsoffset_pos[i] = core.get_position(ztags['pfsoffset'])

        if np.abs(zpos - zdrive_pos[i]) < tolerance:
            # on target
            break
        elif i == max_iter - 1:
            # reached maximum iterations, don't move any more
            break
        # distance to move
        deltaz = zpos - zdrive_pos[i]
        # set the jump factor according to the thresholds
        for th, fac in zip(jump_factor_settings['thresholds'],
                           jump_factor_settings['factors']):
            if np.abs(deltaz) < th:
                jump_factor = fac
                break
        else:
            jump_factor = jump_factor_settings['factors'][-1]
        delta_steps = int(jump_factor * deltaz * approx_stepsperum)
        core.set_position(ztags['pfsoffset'], pfsoffset_pos[i] + delta_steps)
        # TODO implement something like calibrate_pfsoffset.wait_for_focus
        # but only when the PFS focus/on-target Status/State can be retreived
        # on TiE2, until then: just wait
        # time.sleep(0.05). # maybe not; apparently, set_position waits

    return zdrive_pos[:i+1], pfsoffset_pos[:i+1]


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


def mm2zpaint_acq(mmacq, core, ztags):
    """Transform the micromanager acquisition settings to zPAINT
    acquisition settings (perforoming separate multi-d acquisitions)
    Args:
        mmacq : dict
            the micromanager acquisition settings, retrieved by
            get_multid_settings
        core : pycromanager Core instance
            for hardware control
        ztags : dict
            tags for z hardware, keys: 'zdrive', 'pfsoffset'
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
        'Z': get_abs_zplanes(mmacq['Z'], core, ztags),
        'comment': mmacq['comment'],
        'prefix': mmacq['prefix'],
        'root': mmacq['root'],
    }
    return multid_sttg, zpacq


def get_abs_zplanes(z_sttg, core, ztags):
    """Get z-planes from the micromanager z-settings.
    If relative stacks are used, calculate from the current Z position.
    Args:
        z_sttg : dict
            the z-settings as retrieved in get_multid_settings, with keys:
            'slices', 'bot', 'top', 'step', 'relative'
        core : pycromanager Core instance
            for hardware control
        ztags : dict
            tags for z hardware, keys: 'zdrive', 'pfsoffset'
    Returns:
        zplanes : np array
            the absolute zdrive plane positions to move to
    """
    # zplanes = np.linspace(z_sttg['bot'], z_sttg['top'], num=z_sttg['slices'])
    zplanes = np.array(z_sttg['slices'])
    if z_sttg['relative']:
        zplanes = zplanes + core.get_position(ztags['zdrive'])
    return zplanes


def start_acq(core, acq_settings):
    """Starts a zPAINT acquisition, based on the multi-d settings in
    micromanager.

    Args:
        core : pycromanager Core instance
            for hardware control
        acq_settings : dict
            additional settings for the acquisition
            items:
                ztags : dict with keys 'zdrive', 'pfsoffset'
                ztolerance : float, tolerance of z movement in um
                zmaxiter : int, maximum number of iterations for moving in z
    """
    pfson = acq_settings['ztags']['pfson']
    if core.get_property(pfson[0], pfson[1]) != 'On':
        raise ValueError('PFS is not on. Please switch on and restart.')

    studio = Studio(convert_camel_case=False)

    # switch live mode off if it is running, otherwise an error occurs
    if studio.live().is_live_mode_on():
        studio.live().set_live_mode_on(False)

    acq = get_multid_settings(studio)
    multid_sttg, zpacq = mm2zpaint_acq(acq, core, acq_settings['ztags'])

    # create the root folder
    dirpath = os.path.join(zpacq['root'], zpacq['prefix'])
    ext = ''
    extit = 0
    while os.path.exists(dirpath + ext):
        extit += 1
        ext = '_{:d}'.format(extit)
    dirpath = dirpath + ext
    os.makedirs(dirpath)

    # save settings
    acquisition_config = {
        'inner dimensions': multid_sttg.copy(),
        'outer dimensions': zpacq.copy(),
        'acquisition_settings': acq_settings,
    }
    acquisition_config['outer dimensions']['C'] = [str(c) for c in acquisition_config['outer dimensions']['C']]
    acquisition_config['outer dimensions']['S'] = [str(c) for c in acquisition_config['outer dimensions']['S']]
    acquisition_config['outer dimensions']['Z'] = [float(c) for c in acquisition_config['outer dimensions']['Z']]
    print(acquisition_config)
    with open(os.path.join(
            dirpath, 'acquisition_configuration.yaml'), 'w') as f:
        yaml.dump(acquisition_config, f)
    acq_log = {}
    acq_i = 0

    # create multi-d-acquisition events
    print(multid_sttg)
    print('Hard-coded dimension order: PCZT')
    events = multi_d_acquisition_events(
        num_time_points=multid_sttg['T']['n'],
        time_interval_s=multid_sttg['T']['dt'] / 1000)

    z_start = core.get_position(acq_settings['ztags']['pfsoffset'])
    viewer = None

    # fix dimension order to SCZT
    for i_s, pos in zp_iterate_S(zpacq, core):
        for i_c, c in zp_iterate_C(zpacq, core):
            for i_z, z, (zdrive_pos, pfsoffset_pos) in zp_iterate_Z(
                        zpacq, core, acq_settings):
                # add log entries
                acq_log[acq_i] = {
                    'i_s': i_s, 'i_c': i_c, 'i_z': i_z,
                    'pos': str(pos), 'c': str(c), 'z': float(z),
                    'curr_zpos': float(zdrive_pos[-1]),
                    'curr_pfsoffset': float(pfsoffset_pos[-1]),
                    'time': datetime.now().strftime('%y%m%d-%H%M:%S.%f'),
                }
                acq_i += 1
                prefix = zpacq['prefix'] + '_P{:d}_C{:d}_Z{:d}'.format(
                    i_s, i_c, i_z)
                print('acquring dataset ', acq_i)
                with Acquisition(directory=dirpath, name=prefix, show_display=acq_settings['show_display']) as acq:
                    acq.acquire(events)
                    if acq_settings['show_display']:
                        viewer = acq.get_viewer()
                time.sleep(.2)
                if viewer is not None and acq_settings['close_display_after_acquisition']:
                    viewer.close()
                acq_log[acq_i-1]['dummy-acq prefix'] = prefix
                # acq_log[acq_i-1]['dummy-acq events'] = str(events)

    # move back in z to initial position
    core.set_position(acq_settings['ztags']['pfsoffset'], z_start)
    
    # write log
    with open(os.path.join(
            dirpath, 'acquisition_log.yaml'), 'w') as f:
        yaml.dump(acq_log, f)


def zp_iterate_S(acq, core):
    """Iterator over the S dimension of a zpaint acquisition
    Args:
        acq : dict
        core : pycromanager Core instance
            for hardware control
    Yields:
        iteration : int
            the iteration number
        sval : MultiPosition
            the position
    """
    if not acq['usedims']['S'] or acq['S'] == []:
        yield 0, 0
    else:
        for i, pos in enumerate(acq['S']):
            pos.go_to_position(pos, core)
            yield i, pos


def zp_iterate_C(acq, core):
    """Iterator over the C dimension of a zpaint acquisition
    Args:
        acq : dict
        core : pycromanager Core instance
            for hardware control
    Yields:
        iteration : int
            the iteration number
        cval : int
            the the channel index
    """
    if not acq['usedims']['C'] or acq['C'] == []:
        yield 0, 0
    else:
        for i, chan in enumerate(acq['C']):
            chan_group = chan.channel_group()
            filt = chan.config()
            core.set_config(chan_group, filt)
            yield i, chan


def zp_iterate_Z(acq, core, acq_settings):
    """Iterator over the Z dimension of a zpaint acquisition
    Args:
        acq : dict
        core : pycromanager Core instance
            for hardware control
        acq_settings : dict
            additional settings for the acquisition
            items:
                ztags : dict with keys 'zdrive', 'pfsoffset'
                ztolerance : float, tolerance of z movement in um
                zmaxiter : int, maximum number of iterations for moving in z
    Yields:
        iteration : int
            the iteration number
        zval : float
            the PFS offset position
    """
    if not acq['usedims']['Z'] or len(acq['Z']) == 0:
        yield 0, 0
    else:
        for i, zpos in enumerate(acq['Z']):
            # move to z position
            zdrive_pos, pfsoffset_pos = move_pfsoffset(
                zpos, core, acq_settings['ztags'], acq_settings['jump_factor_settings'],
                acq_settings['zmaxiter'], acq_settings['ztolerance'])
            yield i, zpos, (zdrive_pos, pfsoffset_pos)


if __name__ == '__main__':
    core = Core()

    acq_settings = {
        'ztags': {
            'zdrive': 'ZDrive',
            'pfsoffset': 'PFSOffset',
            'pfson': ('PFS', 'FocusMaintenance')
        },
        'jump_factor_settings': {
            'thresholds': (.5, 2),
            'factors': (.2, .4, .6),
        },
        'ztolerance': .025,  # tolerance of z movement in um
        'zmaxiter': 50,  # maximum number of iterations for moving in z
        'show_display': True,  # apparently, this has to be true, otherwise a pycromanager error gets raised
        'close_display_after_acquisition': True,
    }
    start_acq(core, acq_settings)
