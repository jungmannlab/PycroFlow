#!/usr/bin/env python
"""
    PycroFlow/calibrate_pfsoffset.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A calibration routine for mapping Nikon Perfect Focus System (PFS)
    offset values to stage positions (which are calibrated)

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import numpy as np
import matplotlib.pyplot as plt
import time
from pycromanager import Core


def calibrate(core, range_pars=(150, 400, 0.005), sleep=0):
    tag_pfs = 'TIPFSOffset'
    tag_zdrive = 'TIZDrive'
    tag_status = 'TIPFSStatus'
    prop_status = 'State'
    # tag_zpiezo = 'ZStage'
    tag_pfs = 'PFSOffset'
    tag_zdrive = 'ZDrive'
    tag_status = 'PFS'
    prop_status = 'PFS in Range'

    pfs_range = np.arange(*range_pars)
    pfs_range = pfs_range[:, np.newaxis]
    pfs_range = pfs_range.repeat(2, axis=1)
    pfs_range[:, 1] = pfs_range[::-1, 1]
    pfs_range = np.tile(pfs_range, 2)

    zpos = np.nan * np.ones_like(pfs_range)

    for i in range(pfs_range.shape[1]):
        print('round', i)
        for j, pfs in enumerate(pfs_range[:, i]):
            # print(i, 'of', len(pfs_range))
            core.set_position(tag_pfs, float(pfs))
            if wait_for_focus(core, tag_status, prop_status):
                time.sleep(sleep)
                pos = core.get_position(tag_zdrive)
                zpos[j, i] = pos
        plot_calibration(pfs_range, zpos, show=False, range_pars=range_pars, sleep=sleep)
        np.save('pfs_range_{:d}_{:d}_{:d}_sleep{:d}.npy'.format(
            range_pars[0], range_pars[1], int(range_pars[2]*1000), int(sleep*1000)), pfs_range)
        np.save('zpos_{:d}_{:d}_{:d}.npy'.format(
            range_pars[0], range_pars[1], int(range_pars[2]*1000), int(sleep*1000)), zpos)

    return pfs_range, zpos


def plot_calibration(pfs_range, zpos, show=True, range_pars=(150, 400, 0.005), sleep=0):
    fig, ax = plt.subplots(nrows=1)
    for i in range(pfs_range.shape[1]):
        ax.plot(pfs_range[:, i], zpos[:, i], label='round {:d}'.format(i))
    ax.set_xlabel('PFS offset [a.u.]')
    ax.set_ylabel('ZDrive position [µm]')
    ax.legend()
    # fig, ax = plt.subplots(nrows=2)
    # ax[0].plot(pfs_range, zpos)
    # ax[0].set_xlabel('PFS offset [a.u.]')
    # ax[0].set_ylabel('ZDrive position [µm]')
    # ax[1].plot(pfs_range, zpos)
    # ax[1].set_xlabel('PFS offset [a.u.]')
    # ax[1].set_ylabel('Z piezo position [nm]')
    fig.savefig('calibration_{:d}_{:d}_{:d}_sleep{:d}.png'.format(
        range_pars[0], range_pars[1], int(range_pars[2]*1000), int(sleep*1000)))
    if show:
        plt.show()


def wait_for_focus(core, tag_status, prop_status, timeout=1):
    tic = time.time()
    while time.time() - tic < timeout:
        state = core.get_property(tag_status, prop_status)
        if state == 'Off':
            return False
        status = core.get_property(tag_status, prop_status)
        if status == 'Locked in focus':
            return True
        elif status == 'Focus lock failed':
            return False
        elif status == 'In Range':  # check whether 'PFS in Range' is actually the correct property to use on Skylab
            time.sleep(0.02)
            return True

        time.sleep(0.02)
    return False


if __name__ == "__main__":
    core = Core()
    pfs_range, zpos = calibrate(core, range_pars=(1000, 15000, 10), sleep=0)
    #pfs_range, zpos = calibrate(core, range_pars=(150, 600, 0.005), sleep=.2)
