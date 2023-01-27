#!/usr/bin/env python
"""
    PycroFlow/calibrate_pfsoffset.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A calibration routine for mapping Nikon Perfect Focus System (PFS)
    offset values to stage positions (which are calibrated)

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import numpy as np
import maptlotlib.pyplot as plt
import time
from pycrromanager import core


def calibrate(Core):
    tag_pfs = 'TIPFSOffset'
    tag_zdrive = 'TIZDrive'

    pfs_range = np.arange(0, 1000, .5)

    zpos = np.nan * np.ones_like(pfs_range)

    for i, pfs in enumerate(pfs_range):
        Core.set_position(tag_pfs)
        if wait_for_focus(Core):
            pos = Core.get_position(tag_zdrive)
            zpos[i] = [pos]

    return pfs_range, zpos


def plot_calibration(pfs_range, zpos):
    fig, ax = plt.subplots()
    ax.plot(pfs_range, zpos)
    ax.set_xlabel('PFS offset [a.u.]')
    ax.set_ylabel('ZDrive position [µm]')
    plt.show()


def wait_for_focus(Core, timeout=1):
    tic = time.tic()
    while time.time() - tic < timeout:
        state = Core.get_property('TIPFSState', 'State')
        if state == 'Off':
            return False
        status = Core.get_property('TIPFSState', 'Status')
        if status == 'Locked in focus':
            return True
        elif status == 'Focus lock failed':
            return False

        time.sleep(0.02)
    return False


if __name__ == "__main__":
    Core = core()
    pfs_range, zpos = calibrate(Core)
    plot_calibration(pfs_range, zpos)
