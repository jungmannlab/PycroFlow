"""
frontend_cli.py

Provides a command line interface frontend.
"""
import pycroflow.hamilton_architecture as ha
from pycroflow.protocols import ProtocolBuilder
import pycroflow.imaging as im
import pycroflow.illumination as il
from pycroflow.orchestration import ProtocolOrchestrator


def start():
    system_config = ha.legacy_system_config
    tubing_config = ha.legacy_tubing_config

    acquisition_config = {}

    pbuilder = ProtocolBuilder()
    protocol, vols = pbuilder.create_protocol(acquisition_config)

    la = ha.LegacyArchitecture(system_config, tubing_config)
    la._assign_protocol(protocol['fluid'])
    la.fill_tubings()

    imgsys = im.ImagingSystem({}, protocol['img'])

    if protocol.get('illu'):
        illusys = il.IlluminationSystem(protocol['illumination'])
    else:
        illusys = None

    po = ProtocolOrchestrator(
        protocol, imaging_system=imgsys, fluid_system=la,
        illumination_system=illusys)
    po.start_orchestration()
