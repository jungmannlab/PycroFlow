"""
frontend_cli.py

Provides a command line interface frontend.
"""
import os
import cmd
import yaml
import logging
# import NotImplmentedError

import PycroFlow.hamilton_architecture as ha
from PycroFlow.protocols import ProtocolBuilder
import PycroFlow.imaging as im
import PycroFlow.illumination as il
from PycroFlow.orchestration import ProtocolOrchestrator


logger = logging.getLogger(__name__)


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
        * (calibrate_tubings)
        * (load_protocol)
        * fill_tubings
        * start_orchestration
        * start_protocol
        * clean_tubings
        * exit
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
            self.fluid_system._calibrate_tubing(400)
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
        """Start the protocol.
        Args:
            protocol start entries
            e.g. "start_protocol fluid: 5, img: 2"
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return

        start_entries = {}
        if line:
            parts = line.split(',')
            for part in parts:
                syst, step = part.split(':')
                start_entries[syst.strip()] = int(step.strip())
        self.orchestrator.start_protocol(start_entries)

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

    def do_pump(self, arg):
        """Uses a syringe pump to pump from A to B.
        Do not specify arguments with None. No comma.
        pickup/dispense_flushvalve: 0 or 1
        Args:
            pump_name, vol, velocity=None,
              pickup_dir='in', dispense_dir='out',
              pickup_res=None, dispense_res=None,
              pickup_flushvalve=None, dispense_flushvalve=None
        """
        args = arg.split()
        pump_name = args.pop(0)
        if 'pump_name=' in pump_name:
            pump_name = pump_name[len('pump_name='):]
        if ',' in pump_name:
            pump_name = pump_name[:pump_name.index(',')]

        vol = args.pop(0)
        if 'vol=' in vol:
            vol = vol[len('vol='):]
        if ',' in vol:
            vol = vol[:vol.index(',')]
        vol = float(vol)

        kwargs = {ar.split('=')[0]: ar.split('=')[1] for ar in args}
        logger.debug(str(kwargs))
        if 'velocity' in kwargs.keys():
            if ',' in kwargs['velocity']:
                kwargs['velocity'] = kwargs['velocity'][:kwargs['velocity'].index(',')]
            kwargs['velocity'] = int(kwargs['velocity'])
        if 'pickup_res' in kwargs.keys():
            if ',' in kwargs['pickup_res']:
                kwargs['pickup_res'] = kwargs['pickup_res'][:kwargs['pickup_res'].index(',')]
            kwargs['pickup_res'] = int(kwargs['pickup_res'])
        if 'pickup_dir' in kwargs.keys():
            if ',' in kwargs['pickup_dir']:
                kwargs['pickup_dir'] = kwargs['pickup_dir'][:kwargs['pickup_dir'].index(',')]
            try:
                kwargs['pickup_dir'] = int(kwargs['pickup_dir'])
            except:
                pass
        if 'dispense_res' in kwargs.keys():
            if ',' in kwargs['dispense_res']:
                kwargs['dispense_res'] = kwargs['dispense_res'][:kwargs['dispense_res'].index(',')]
            kwargs['dispense_res'] = int(kwargs['dispense_res'])
        if 'dispense_dir' in kwargs.keys():
            if ',' in kwargs['dispense_dir']:
                kwargs['dispense_dir'] = kwargs['dispense_dir'][:kwargs['dispense_dir'].index(',')]
            try:
                kwargs['dispense_dir'] = int(kwargs['dispense_dir'])
            except:
                pass
        if 'pickup_flushvalve' in kwargs.keys():
            if ',' in kwargs['pickup_flushvalve']:
                kwargs['pickup_flushvalve'] = kwargs['pickup_flushvalve'][:kwargs['pickup_flushvalve'].index(',')]
            kwargs['pickup_flushvalve'] = bool(int(kwargs['pickup_flushvalve']))
        if 'dispense_flushvalve' in kwargs.keys():
            if ',' in kwargs['dispense_flushvalve']:
                kwargs['dispense_flushvalve'] = kwargs['dispense_flushvalve'][:kwargs['dispense_flushvalve'].index(',')]
            kwargs['dispense_flushvalve'] = bool(int(kwargs['dispense_flushvalve']))

        if not self.orchestrator:
            print('Start orchestration first.')
            return

        if hasattr(self.orchestrator.fluid_system, pump_name):
            pump = getattr(self.orchestrator.fluid_system, pump_name)
        elif hasattr(self.orchestrator.fluid_system, 'pump_' + pump_name):
            pump = getattr(self.orchestrator.fluid_system, 'pump_' + pump_name)
        else:
            print('Cannot find pump ' + pump_name)
            return
        kwargs['pump'] = pump
        kwargs['vol'] = vol

        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system._pump,
            kwargs=kwargs)

    def do_inject(self, arg):
        """Do an injection, pumping in and out
        Args:
            vol : float
                the volume to inject in µl
            velocity : float
                the flow velocity of the injection in µl/min
            extractionfactor : float
                the factor of flow speeds of extraction vs injection.
                a non-perfectly calibrated system may result in less volume
                being extracted than injected, leading to spillage. To prevent
                this, the extraction needle could be positioned higher, and
                extraction performed faster than injection
        """
        args = arg.split()
        vol = args.pop(0)
        if 'vol=' in vol:
            vol = vol[len('vol='):]
        vol = float(vol)
        kwargs = {ar.split('=')[0]: ar.split('=')[1] for ar in args}
        kwargs['vol'] = vol
        if 'velocity' in kwargs.keys():
            if ',' in kwargs['velocity']:
                kwargs['velocity'] = kwargs['velocity'].index(',')
            kwargs['velocity'] = int(kwargs['velocity'])
        if 'extractionfactor' in kwargs.keys():
            if ',' in kwargs['extractionfactor']:
                kwargs['extractionfactor'] = kwargs['extractionfactor'].index(',')
            kwargs['extractionfactor'] = int(kwargs['extractionfactor'])

        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system._inject,
            kwargs=kwargs)

    def do_set_valves(self, arg):
        """Set the valves to access the reservoir specified
        Args:
            reservoir_id : int
                the reservoir to set to
        """
        reservoir_id = int(arg)
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system._set_valves,
            kwargs={'reservoir_id': reservoir_id})

    def do_deliver(self, reservoir_id, volume):
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system.deliver_fluid,
            kwargs={'reservoir_id': reservoir_id, 'volume': volume})

    def do_clean(self, line=''):
        """Perform the complete cleaning protocol. The configuration must
        specify special reservoir names for 'h2o', 'ipa', 'rbs', 'empty'
        """
        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system.clean_tubings)

    def do_clean_tubings(self, arg):
        """Fill and empty all tubings with liquid from one or more
        reservoirs.
        Args:
            extra_vol : int (default 0)
                the volume to use on top of the tubing volume
            cleaning_reservoirs : int or list of int (default [])
                the reservoir IDs of the cleaning liquids to use.
            reservoir_vol : int (optional)
                the volume of the reservoirs. If given, this volue
                is extracted initially to make sure the reservoirs
                are empty
            empty_finally : bool (default: True)
                Whether to empty the tubings in the end or leave the
                last liquid in

        Do not specify arguments with None. No comma.
        pickup/dispense_flushvalve: 0 or 1

        Example Command:
        $ clean_tubings 200 cleaning_reservoirs=12
        or
        $ clean_tubings extra_vol=200 cleaning_reservoirs=11,12 empty_finally=0
        """
        args = arg.split()
        extra_vol = args.pop(0)
        if 'extra_vol=' in extra_vol:
            extra_vol = extra_vol[len('extra_vol='):]
        extra_vol = float(extra_vol)
        kwargs = {ar.split('=')[0].strip(): ar.split('=')[1].strip()
                  for ar in args}
        kwargs['extra_vol'] = extra_vol
        if 'cleaning_reservoirs' in kwargs.keys():
            clres = kwargs['cleaning_reservoirs']
            if '[' in clres and ']' in clres:
                clres = clres[clres.index('['):clres.index(']')]
            if ',' in clres:
                clres = clres.split(',')
            else:
                clres = [clres]
            kwargs['cleaning_reservoirs'] = [int(r) for r in clres]
        if 'reservoir_vol' in kwargs.keys():
            if ',' in kwargs['reservoir_vol']:
                kwargs['reservoir_vol'] = kwargs['reservoir_vol'].index(',')
            kwargs['reservoir_vol'] = int(kwargs['reservoir_vol'])
        if 'empty_finally' in kwargs.keys():
            if ',' in kwargs['empty_finally']:
                kwargs['empty_finally'] = kwargs['empty_finally'].index(',')
            kwargs['empty_finally'] = bool(int(kwargs['empty_finally']))

        if not self.orchestrator:
            print('Start orchestration first.')
            return
        self.orchestrator.execute_system_function(
            'fluid', self.fluid_system.clean_tubings_seperate_res,
            kwargs=kwargs)

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
