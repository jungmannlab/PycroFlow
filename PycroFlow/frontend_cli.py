"""
frontend_cli.py

Provides a command line interface frontend.
"""
import os
import cmd
import yaml
# import NotImplmentedError

import PycroFlow.hamilton_architecture as ha
from PycroFlow.protocols import ProtocolBuilder
import PycroFlow.imaging as im
import PycroFlow.illumination as il
from PycroFlow.orchestration import ProtocolOrchestrator


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


class PycroFlowInteractive(cmd.Cmd):
    """Command-line interactive power setting.
    """
    intro = '''Welcome to PycroFlowInteractive.
        Use this to orchestrate fluid dispension, acquisition
        and illumination.

        Typical workflow:
        * load protocol
        * (calibrate tubings)
        * fill tubings
        * start orchestration
        * start protocol
        '''
    prompt = '(PycroFlow)'
    fluid_system = None
    imaging_system = None
    illumination_system = None
    orchestrator = None

    protocol = None
    hamilton_config = None
    tubing_config = None

    def __init__(self):
        super().__init__()
        files = os.listdir()
        if (('hamilton_config.yaml' in files
             and 'tubing_config.yaml' in files)):
            print('auto-loading Hamilton Fluid system.')
            self.do_load_hamilton(
                'hamilton_config.yaml', 'tubing_config.yaml')
        if 'imaging_config.yaml' in files:
            self.do_load_imaging('imaging_config.yaml')

    def do_load_protocol(self, fname_protocol):
        """Load the Fluid Automation protocol
        """
        with open(fname_protocol, 'r') as f:
            self.protocol = yaml.full_load(f)
        if self.fluid_system:
            self.fluid_system._assign_protocol(self.protocol['fluid'])
        if self.imaging_system:
            self.imaging_system._assign_protocol(self.protocol['img'])

        if self.protocol.get('illu'):
            self.illumination_system = il.IlluminationSystem(
                self.protocol['illu'])
        else:
            self.illumination_system = None

    def do_load_hamilton(self, fname_hamilton, fname_tubing):
        """Load the Hamilton Fluid System configuration

        Args:
            fname_hamilton : str (or dict)
                the filename to the hamilton system configuration file
                alternatively, this can be the config itself as a dict
            fname_tubing : str (or dict)
                the filename to the hamilton system tubing configuration file
                alternatively, this can be the config itself as a dict
        """
        if isinstance(fname_hamilton, str):
            with open(fname_hamilton, 'r') as f:
                hamilton_config = yaml.full_load(f)
        else:
            hamilton_config = fname_hamilton
        if isinstance(fname_tubing, str):
            with open(fname_tubing, 'r') as f:
                tubing_config = yaml.full_load(f)
        else:
            tubing_config = fname_tubing

        interface = hamilton_config['interface']
        ha.connect(interface['COM'], interface['baud'])
        if hamilton_config['system_type'] == 'legacy':
            self.fluid_system = ha.LegacyArchitecture(
                hamilton_config, tubing_config)
        else:
            raise NotImplmentedError(
                'System type "' + hamilton_config['system_type']
                + '" is not implemented.')
        if self.protocol:
            self.fluid_system._assign_protocol(self.protocol['fluid'])

    def do_fill_tubings(self, line):
        """Fill the tubings of the fluid system
        """
        if self.fluid_system:
            self.fluid_system.fill_tubings()
        else:
            print('Fluid system needs to be initialized first.')

    def do_calibrate_tubings(self, line):
        """Calibrate the tubings of the fluid system
        """
        if self.fluid_system:
            self.fluid_system._calibrate_tubing(500)
        else:
            print('Fluid system needs to be initialized first.')

    def do_load_imaging(self, fname_imaging):
        """Load the imaging system

        Args:
            fname_imaging : str (or dict)
                the filename to the imaging configuration file
                alternatively, this can be the configuration dict itself
        """
        if isinstance(fname_imaging, str):
            with open(fname_imaging, 'r') as f:
                imaging_config = yaml.full_load(f)
        else:
            imaging_config = fname_imaging

        self.imaging_system = im.ImagingSystem(imaging_config)

    def do_start_orchestration(self, line):
        """Start the orchestration of the systems.
        """
        if not self.protocol:
            print('Load the protocol first.')
            return
        self.orchestrator = ProtocolOrchestrator(
            self.protocol, imaging_system=self.imaging_system,
            fluid_system=self.fluid_system,
            illumination_system=self.illumination_system)
        self.orchestrator.start_orchestration()

    def do_abort_orchestration(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.abort_orchestration()

    def do_start_protocol(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.run_protocol()

    def do_pause_protocol(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.pause_protocol()

    def do_resume_protocol(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.resume_protocol()

    def do_abort_protocol(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.abort_protocol()

    def do_is_protocol_done(self, line):
        """Start the protocol
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        print(self.orchestrator.poll_protocol_finished())

    # ######################### Direct Fluid Manipulation

    def do_pump(self, args):
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            self.fluid_system._pump,
            args)

    def do_deliver(self, reservoir_id, volume):
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            self.fluid_system.deliver_fluid,
            kwargs={'reservoir_id': reservoir_id, 'volume': volume})

    # ######################### Shut down

    def do_exit(self, line):
        """Exit the interaction
        """
        self.close()
        return True

    def precmd(self, line):
        return line

    def close(self):
        if self.orchestrator:
            if self.orchestrator.poll_protocol_finished():
                self.orchestrator.end_orchestration()
            else:
                self.orchestrator.abort_orchestration()


if __name__ == '__main__':
    PycroFlowInteractive().cmdloop()
