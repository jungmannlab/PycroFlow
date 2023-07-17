"""
frontend_cli.py

Provides a command line interface frontend.
"""
import pycroflow.hamilton_architecture as ha
import pycroflow.protocols
import pycroflow.imaging as im
import pycroflow.illumination as il


def start():
    system_config = ha.legacy_system_config
    tubing_config = ha.legacy_tubing_config

    protocol = protocols.create_protocol()

    la = ha.LegacyArchitecture(system_config, tubing_config)
    la._assign_protocol(protocol['fluid'])

    imgsys = im.ImagingSystem({}, protocol['img'])
    # illusys = il.IlluminationSystem(protocol['illu'])
