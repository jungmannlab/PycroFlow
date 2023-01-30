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


def calibrate(core):
    tag_pfs = 'TIPFSOffset'
    tag_zdrive = 'TIZDrive'
    tag_zpiezo = 'ZStage'

    pfs_range = np.arange(0, 700, .025)
    pfs_range = np.concatenate([pfs_range, pfs_range[::-1], pfs_range, pfs_range[::-1], pfs_range, pfs_range[::-1]])

    zpos = np.nan * np.ones_like(pfs_range)
    zpos_piezo = np.nan * np.ones_like(pfs_range)

    for i, pfs in enumerate(pfs_range):
        # print(i, 'of', len(pfs_range))
        core.set_position(tag_pfs, pfs)
        if wait_for_focus(core):
            # time.sleep(.1)
            pos = core.get_position(tag_zdrive)
            zpos[i] = pos
            # pos = core.get_position(tag_zpiezo)
            zpos_piezo[i] = pos

    return pfs_range, zpos, zpos_piezo


def plot_calibration(pfs_range, zpos, zpos_piezo):
    fig, ax = plt.subplots(nrows=1)
    ax.plot(pfs_range, zpos)
    ax.set_xlabel('PFS offset [a.u.]')
    ax.set_ylabel('ZDrive position [µm]')
    # fig, ax = plt.subplots(nrows=2)
    # ax[0].plot(pfs_range, zpos)
    # ax[0].set_xlabel('PFS offset [a.u.]')
    # ax[0].set_ylabel('ZDrive position [µm]')
    # ax[1].plot(pfs_range, zpos)
    # ax[1].set_xlabel('PFS offset [a.u.]')
    # ax[1].set_ylabel('Z piezo position [nm]')
    fig.savefig('calibration_step0p025.png')
    plt.show()


def wait_for_focus(core, timeout=1):
    tic = time.time()
    while time.time() - tic < timeout:
        state = core.get_property('TIPFSStatus', 'State')
        if state == 'Off':
            return False
        status = core.get_property('TIPFSStatus', 'Status')
        if status == 'Locked in focus':
            return True
        elif status == 'Focus lock failed':
            return False

        time.sleep(0.02)
    return False


if __name__ == "__main__":
    core = Core()
    pfs_range, zpos, zpos_piezo = calibrate(core)
    np.save('pfs_range.npy', pfs_range)
    np.save('zpos.npy', zpos)
    plot_calibration(pfs_range, zpos, zpos_piezo)
