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
import numpy as np
import cmd
from pycromanager import Core


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

    return zdrive_pos[:i + 1], pfsoffset_pos[:i + 1]


def get_abs_zpos(core, ztags):
    """Get the current z-position.
    Args:
        core : pycromanager Core instance
            for hardware control
        ztags : dict
            tags for z hardware, keys: 'zdrive', 'pfsoffset'
    Returns:
        the absolute zdrive position
    """
    return core.get_position(ztags['zdrive'])


class PFSmove(cmd.Cmd):
    """Command-line interactive power setting.
    """
    intro = '''Welcome to PFSmove. Use this to move absolute or relative
        in z while haveing PFS on.
        '''
    prompt = '(PFS move)'
    file = None

    def __init__(self, core, acq_settings):
        super().__init__()
        self.core = core
        self.acq_settings = acq_settings

    def do_pos(self, line):
        """Get the current ZDrive position
        """
        zpos = get_abs_zpos(self.core, self.acq_settings['ztags'])
        print('z pos: ', zpos, 'um')

    def do_abs(self, zpos):
        """Absolute move
        Args:
            zpos : float
                the position to move to in um.
        """
        zpos = float(zpos)
        move_pfsoffset(
            zpos, self.core, self.acq_settings['ztags'],
            self.acq_settings['jump_factor_settings'],
            acq_settings['zmaxiter'], acq_settings['ztolerance'],
            approx_stepsperum=1000)

    def do_rel(self, zmove):
        """Relative move
        Args:
            zmove : int
                the distance to move by in nm
        """
        zmove = float(zmove) / 1000
        curr_z = get_abs_zpos(self.core, self.acq_settings['ztags'])
        move_pfsoffset(
            curr_z + zmove, self.core, self.acq_settings['ztags'],
            self.acq_settings['jump_factor_settings'],
            acq_settings['zmaxiter'], acq_settings['ztolerance'],
            approx_stepsperum=1000)

    def do_exit(self, line):
        """Exit the interaction
        """
        self.close()
        return True

    def precmd(self, line):
        return line

    def close(self):
        pass


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
    }
    PFSmove(core, acq_settings).cmdloop()
