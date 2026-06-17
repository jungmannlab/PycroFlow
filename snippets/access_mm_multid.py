#!/usr/bin/env python
"""
    PycroFlow/access_mm_multid.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    access the multi d acquisition entries of micromanager

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
from pycromanager import Studio


def get_multid():
    studio = Studio(convert_camel_case=False)
    acqmgr = studio.getAcquisitionManager()
    acqsttgs = acqmgr.getAcquisitionSettings()  

    # https://valelab4.ucsf.edu/~MM/doc-2.0.0-gamma/mmstudio/org/micromanager/acquisition/SequenceSettings.html
    acqorder = acqsttgs.acqOrderMode()
    acqordermap = {
        0: 'TSZC',
        1: 'TSCZ',
        2: 'STZC',
        3: 'STCZ'
    }

    usedims = {}
    usedims['C'] = acqsttgs.useChannels()
    usedims['T'] = acqsttgs.useFrames()
    usedims['S'] = acqsttgs.usePositionList()
    usedims['Z'] = acqsttgs.useSlices()

    print('channels', [acqsttgs.channels().get(i) for i in range(acqsttgs.channels().size())])

    z_definition = {
        'slices': [acqsttgs.slices().get(i) for i in range(acqsttgs.slices().size())],
        'bot': acqsttgs.sliceZBottomUm(),
        'step': acqsttgs.sliceZStepUm(),
        'top': acqsttgs.sliceZTopUm(),
        'relative': acqsttgs.relativeZSlice()
    }

    positions = studio.getPositionListManager().getPositionList().getPositions()
    print(positions)

    print('usedims', usedims)

    return acqorder, z_definition


if __name__ == '__main__':
    acqorder, z_definition = get_multid()
    print(acqorder)
    print(z_definition)