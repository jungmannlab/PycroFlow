"""
protocols.py

Transforms aggregated protocols (Exchange-PAINT, MERPAINT, ...)
into linearized protocols for the various subsystems (fluidics, imaging,
illumination)

e.g.

fluid_settings = {
    'vol_wash': 500,  # in ul
    'vol_imager_pre': 500,  # in ul
    'vol_imager_post': 100,  # in ul
    'wait_after_pickup': 10,  # wait time to let fluid settle, in s
    'vol_remove_before_wash': 500,  # volume to remove before
    # washing, and add after washing, to improve efficiency
    'reservoir_names': {
        1: 'R1', 3: 'R3', 5: 'R5', 6: 'R6',
        7: 'R2', 8: 'R4', 9: 'Res9', 10: 'Buffer B+'},
    'experiment' : {
        'type': 'Exchange',  # options: ['Exchange', 'MERPAINT', 'FlushTest']
        'wash_buffer': 'Buffer B+',
        'imagers': [
            'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2', 'R4', 'R2'],
        'initial_imager': 'Rx',  # if string, do an acquisition before fluid
}
imaging_settings ={
    'frames': 50000,
    't_exp': 100,  # in ms
    'ROI': [512, 512, 512, 512],
}
illumination_settings = {
    'setup': 'Mercury',
    'laser': 560,
    'power': 30,  #mW
}

flow_acq_config = {
    'save_dir': r'Z://users//grabmayr//microscopy_data',
    'base_name': 'AutomationTest_R2R4',
    'fluid_settings': fluid_settings,
    'imaging_settings': imaging_settings,
    'illumination_settings': illumination_settings,
    'mm_parameters': {
        'channel_group': 'Filter turret',
        'filter': '2-G561',
    },
}


the result will be e.g.
protocol_fluid = [
    {'$type': 'inject', 'reservoir_id': 0, 'volume': 500},
    {'$type': 'incubate', 'duration': 120},
    {'$type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600},
    {'$type': 'signal', 'value': 'fluid round 1 done'},
    {'$type': 'flush', 'flushfactor': 1},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
    {'$type': 'inject', 'reservoir_id': 20, 'volume': 500},
]

protocol_imaging = [
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
    {'$type': 'signal', 'value': 'imaging round 1 done'},
]

protocol_illumination = [
    {'$type': 'power', 'value': 1},
    {'$type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'$type': 'power', 'value': 50},
    {'$type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
]

"""

import os
from datetime import datetime

import yaml

# import ic
# import logging
from loguru import logger

from PycroFlow.schemas import validate_protocol

# logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


class ProtocolBuilder:
    def __init__(self):
        # Each subsystem's step list. create_step_X methods .append() into
        # these; create_steps() reinitializes them to lists too. Initializing
        # as dicts (the pre-Stage-2 bug) made every test that called
        # create_step_X directly without going through create_steps fail
        # with "dict has no attribute append".
        self.steps = {"fluid": [], "img": [], "illu": []}
        self.reservoir_vols = {}

    def build_protocol(self, config):
        """Compile a config into a validated Run Sequence dict (no I/O).

        Parameters
        ----------
        config : dict
            The experiment design (``save_dir``, ``base_name``, and the
            ``fluid`` / ``img`` / ``illu`` sections each with
            ``parameters`` / ``settings``).

        Returns
        -------
        dict
            The linearized protocol (per-subsystem ``protocol_entries`` +
            ``parameters``), schema-validated via :func:`validate_protocol`.
        """
        steps, reservoir_vols = self.create_steps(config)
        protocol = {}
        if "fluid" in config.keys():
            protocol["fluid"] = {"protocol_entries": steps["fluid"]}
            if "parameters" in config["fluid"].keys():
                protocol["fluid"]["parameters"] = config["fluid"]["parameters"]
        if "img" in config.keys():
            protocol["img"] = {"protocol_entries": steps["img"]}
            if "parameters" in config["img"].keys():
                protocol["img"]["parameters"] = config["img"]["parameters"]
        if "illu" in config.keys():
            protocol["illu"] = {"protocol_entries": steps["illu"]}
            if "parameters" in config["illu"].keys():
                protocol["illu"]["parameters"] = config["illu"]["parameters"]

        # Pin the wire format: catch malformed entries (unknown $type, missing
        # required fields, typos in field names) here before the orchestrator
        # picks them up mid-run. Schema-validation only — protocol dict is
        # unchanged.
        validate_protocol(protocol)
        return protocol

    def create_protocol(self, config):
        """Compile a config and write the Run Sequence to a YAML file.

        Thin wrapper around :meth:`build_protocol` that also persists the
        result. Kept for the CLI / ``start_experiment.py`` flow.

        Parameters
        ----------
        config : dict
            See :meth:`build_protocol`. Additionally needs ``save_dir`` and
            ``base_name`` for the output filename.

        Returns
        -------
        fname : str
            Filename of the saved protocol.
        steps : dict
            The per-subsystem step lists.
        """
        protocol = self.build_protocol(config)

        # save protocol
        fname = (
            config["base_name"]
            + datetime.now().strftime("_%y%m%d-%H%M")
            + ".yaml"
        )
        filename = os.path.join(config["save_dir"], fname)

        with open(filename, "w") as f:
            yaml.dump(
                protocol,
                f,
                default_flow_style=True,
                canonical=True,
                default_style='"',
            )

        return fname, self.steps

    # Registry mapping the user-facing experiment type (case-insensitive)
    # to the ProtocolBuilder method that knows how to expand it. New
    # experiment types add an entry here instead of editing the dispatcher,
    # and external code can extend the registry from outside (Stage 4's
    # plugin path).
    EXPERIMENT_TYPES = {
        "exchange": "create_steps_exchange",
        "merpaint": "create_steps_MERPAINT",
        "flushtest": "create_steps_flushtest",
        "sph-resi": "create_steps_sph_resi",
    }

    def create_steps(self, config):
        """Create the protocol steps one after another.

        Parameters
        ----------
        config : dict
            Flow acquisition configuration with keys ``save_dir``,
            ``base_name``, ``fluid_settings``, ``imaging_settings``,
            ``illumination_settings`` and ``mm_parameters``.

        Returns
        -------
        steps : list of dict
            The aria steps.
        reservoir_vols : dict
            Maps reservoir names to volumes.
        """
        self.steps = {"fluid": [], "img": [], "illu": []}
        self.reservoir_vols = {
            id: 0 for id in config["fluid"]["settings"]["reservoir_names"]
        }
        exptype = config["fluid"]["settings"]["experiment"]["type"]
        method_name = self.EXPERIMENT_TYPES.get(exptype.lower())
        if method_name is None:
            raise KeyError(
                "Experiment type {!r} not implemented. Known types: {}".format(
                    exptype, sorted(self.EXPERIMENT_TYPES)
                )
            )
        return getattr(self, method_name)(config)

    def create_stepset_acquisition(
        self, illusttg, imgsttg, unique_name, readable_name, fluid_wait=True,
        name=None,
    ):
        """Create the step set for an acquisition.

        Parameters
        ----------
        illusttg : dict or None
            Illumination settings with keys ``'laser'``, ``'power'``,
            ``'warmup_delay'`` and optionally ``'shutter_off_nonacq'``.
        imgsttg : dict
            Imaging settings with keys ``'frames'`` and ``'t_exp'``.
        unique_name : str or int
            A unique name for the acquisition. In plain Exchange-PAINT,
            this can be the round index; for SPH-RESI, it needs to be the
            tgt_round and resi-round combination; for dark-round imaging
            ``'dark'`` should be prepended.
        readable_name : str or int
            A readable name for what happens this round. In plain
            Exchange-PAINT, this can be the imager.
        fluid_wait : bool
            Whether the fluid system should await the completion of the
            acquisition.
        name : str, optional
            Human-readable description of this round (e.g. ``'R1'`` or
            ``'A1 RESI round 2'``), stored on the acquire entry so the GUI can
            label the round. Defaults to ``readable_name``.
        """
        if name is None:
            name = str(readable_name)
        if illusttg:
            self.create_step_setpower(
                illusttg["laser"],
                illusttg["power_acq"],
                illusttg["warmup_delay"],
                message=f"for imaging {readable_name}",
            )
            if illusttg.get("shutter_off_nonacq"):
                self.create_step_setshutter(state=True)
            self.create_step_signal(
                system="illu",
                message=f"done setting power round {unique_name}",
            )
            self.create_step_waitfor_signal(
                system="img",
                target="illu",
                message=f"done setting power round {unique_name}",
            )
        self.create_step_acquire(
            imgsttg["frames"],
            imgsttg["t_exp"],
            message=f"round_{unique_name}-{readable_name}",
            name=name,
        )
        self.create_step_signal(
            system="img", message=f"done imaging round {unique_name}"
        )
        if fluid_wait:
            logger.debug(
                f"{unique_name} {readable_name} adding fluid wait step"
            )
            self.create_step_waitfor_signal(
                system="fluid",
                target="img",
                message=f"done imaging round {unique_name}",
            )
        if illusttg:
            self.create_step_waitfor_signal(
                system="illu",
                target="img",
                message=f"done imaging round {unique_name}",
            )
            self.create_step_setpower(
                illusttg["laser"],
                illusttg["power_nonacq"],
                illusttg["warmup_delay"],
            )
            if illusttg.get("shutter_off_nonacq"):
                self.create_step_setshutter(state=False)

    def create_stepset_flush(
        self,
        volumes,
        res_idcs,
        wait_after_pickup,
        reagent,
        washing,
        t_incubate=0,
        unique_name=None,
        readable_name=None,
        img_wait=True,
        illu_wait=False,
    ):
        """Flush liquid through the sample chamber.

        Optionally, this can be followed by a waiting (incubation) step and a
        signalling step.

        Parameters
        ----------
        volumes : dict
            The volumes dictionary, with keys ``'vol_remove_before_flush'``,
            ``'vol_wash'`` and ``'vol_reagent'``.
        res_idcs : dict
            Maps from reservoir names to reservoir indices.
        wait_after_pickup : int
            Seconds to wait for equilibration.
        reagent : str
            Reservoir name of the reagent to flush (or wash buffer).
        washing : bool
            Whether to use wash volume or reagent volume.
        t_incubate : int
            Minutes to incubate after flushing.
        unique_name : str
            A unique name of the flushing step. This is used for the
            ``'fluid'`` system signal and must be the same that is going to
            be waited for in another system.
        img_wait : bool
            Whether the imaging system should wait for this step to finish.
        illu_wait : bool
            Whether the illumination system should wait for this step to
            finish.
        """
        if washing:
            vol = volumes["vol_wash"]
        else:
            vol = volumes["vol_reagent"]
        self.create_step_pumpout(
            volume=volumes["vol_remove_before_flush"], extractionfactor=1
        )
        self.create_step_inject(
            volume=vol - volumes["vol_remove_before_flush"],
            reservoir_id=res_idcs[reagent],
            delay=wait_after_pickup,
        )
        self.create_step_inject(
            volume=volumes["vol_remove_before_flush"],
            reservoir_id=res_idcs[reagent],
            extractionfactor=0,
            delay=wait_after_pickup,
        )
        if t_incubate > 0:
            self.create_step_incubate(t_incubate)
        if unique_name is not None:
            logger.debug(f"{unique_name} {readable_name} creating signal")
            self.create_step_signal(
                system="fluid", message=f"done flushing {unique_name}"
            )
            if img_wait:
                logger.debug(
                    f"{unique_name} {readable_name} adding img wait step"
                )
                self.create_step_waitfor_signal(
                    system="img",
                    target="fluid",
                    message=f"done flushing {unique_name}",
                )
            if illu_wait:
                logger.debug(
                    f"{unique_name} {readable_name} adding illu wait step"
                )
                self.create_step_waitfor_signal(
                    system="illu",
                    target="fluid",
                    message=f"done flushing {unique_name}",
                )

    def create_steps_exchange(self, config):
        """Create the protocol steps for an Exchange-PAINT experiment.

        Parameters
        ----------
        config : dict
            Flow acquisition configuration with keys ``save_dir``,
            ``base_name``, ``fluid_settings``, ``imaging_settings``,
            ``illumination_settings`` and ``mm_parameters``.

        Returns
        -------
        steps : list of dict
            The aria steps.
        reservoir_vols : dict
            Maps reservoir names to volumes.

        Examples
        --------
        Example ``config``::

        config = {
            "fluid": {
                'parameters': {
                    'start_velocity': 50,
                    'max_velocity': 200,
                    'stop_velocity': 50,
                    'pumpout_dispense_velocity': 200,
                    'clean_velocity': 1500,
                    'mode': 'tubing_ignore',
                    # 'tubing_stack', 'tubing_flush' or 'tubing_ignore'
                    'extractionfactor': 4},
                'settings': {
                    'vol_wash_pre': 2,  # in ul
                    'vol_wash': 7,  # in ul
                    'vol_imager_pre': 7,  # in ul
                    'vol_imager_post': 5,  # in ul
                    'reservoir_names': {
                        0: 'R1', 1: 'R2', 2: 'R3', 3: 'R4',
                        4: 'R5', 5: 'Imaging buffer', 6: 'R6'},
                    'experiment' : {
                        'type': 'Exchange',
                        # options: ['Exchange', 'MERPAINT', 'FlushTest']
                        'wash_buffer': 'Imaging buffer',
                        'imagers': [
                            'R1', 'R2', 'R3', 'R4', 'R5', 'R6'],}
                }
            },
            "img": {
                'parameters': {
                    'show_progress': True,
                    'show_display': True,
                    'close_display_after_acquisition': True,
                    },
                'settings': {
                    'frames': 6,
                    'darkframes': 5,
                    't_exp': 100,  # in ms
                    }
            },
            "illu": {
                'parameters': {
                    'channel_group': 'Filter turret',
                    'filter': '2-G561',
                    'ROI': [512, 512, 512, 512]},
                'settings': {
                    'setup': 'Mercury',
                    'laser': 560,
                    'power': 30,  #mW
                    }
            }
        """
        experiment = config["fluid"]["settings"]["experiment"]
        reservoirs = config["fluid"]["settings"]["reservoir_names"]

        wait_after_pickup = config["fluid"]["settings"].get(
            "wait_after_pickup", 0
        )

        # volumes = {
        #     'vol_remove_before_flush': config['fluid']['settings'].get(
        #         'vol_remove_before_flush', 0),
        #     'vol_reagent': config['fluid']['settings']['vol_reagent'],
        #     'vol_wash': config['fluid']['settings']['vol_wash'],
        #     'vol_wash_pre': config['fluid']['settings']['vol_wash_pre'],
        # }

        volumes = {
            "vol_remove_before_flush": config["fluid"]["settings"].get(
                "vol_remove_before_flush", 0
            ),
            # 'vol_reduction': config['fluid']['settings'].get(
            #     'vol_remove_before_wash', 0),
            "vol_wash": config["fluid"]["settings"]["vol_wash"],
            "vol_reagent": config["fluid"]["settings"]["vol_imager_post"],
        }

        initial_imager = experiment.get("initial_imager")

        # imgsttg = {
        #     'frames': config['img']['settings']['frames'],
        #     't_exp': config['img']['settings']['t_exp']}
        darkimgsttg = {
            "frames": config["img"]["settings"]["darkframes"],
            "t_exp": config["img"]["settings"]["t_exp"],
        }
        illusttg = config.get("illu", {}).get("settings")

        # check that all mentioned sources acqually exist
        assert experiment["wash_buffer"] in reservoirs.values()
        assert all(
            [name in reservoirs.values() for name in experiment["imagers"]]
        )

        washbuf = experiment["wash_buffer"]
        # res_idcs = {name: nr - 1 for nr, name in reservoirs.items()}
        res_idcs = {name: nr for nr, name in reservoirs.items()}

        # if the sample already has the first imager, direcly start imaging
        if isinstance(initial_imager, str):
            round = 0
            imager = initial_imager
            logger.debug(f"adding initial imager {imager}")

            if isinstance(config["img"]["settings"]["frames"], dict):
                imgsttg = {
                    "frames": config["img"]["settings"]["frames"][imager],
                    "t_exp": config["img"]["settings"]["t_exp"],
                }
            else:
                imgsttg = {
                    "frames": config["img"]["settings"]["frames"],
                    "t_exp": config["img"]["settings"]["t_exp"],
                }

            self.create_stepset_acquisition(
                illusttg,
                imgsttg,
                unique_name=f"img-{round}",
                readable_name=imager,
                fluid_wait=True,
            )
            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=washbuf,
                washing=True,
                unique_name=f"img-{round}",
                readable_name=imager,
                img_wait=True,
                illu_wait=True,
            )
            # check dark frames
            self.create_stepset_acquisition(
                illusttg,
                darkimgsttg,
                unique_name=f"dark-{round}",
                readable_name=imager,
                fluid_wait=True,
                name=f"{imager} (dark frames)",
            )
        else:
            # self.create_step_pumpout(volume=volumes['vol_wash_pre'])
            # self.create_step_inject(
            #     volume=volumes['vol_wash_pre'],
            #     reservoir_id=res_idcs[washbuf])
            logger.debug("No initial imager")
            pass

        for round, imager in enumerate(experiment["imagers"]):
            if round < len(experiment["imagers"]) - 1:
                last_round = False
            else:
                last_round = True
            if isinstance(initial_imager, str):
                round = round + 1

            if isinstance(config["img"]["settings"]["frames"], dict):
                imgsttg = {
                    "frames": config["img"]["settings"]["frames"][imager],
                    "t_exp": config["img"]["settings"]["t_exp"],
                }
            else:
                imgsttg = {
                    "frames": config["img"]["settings"]["frames"],
                    "t_exp": config["img"]["settings"]["t_exp"],
                }

            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=imager,
                washing=False,
                unique_name=f"img-{round}",
                readable_name=imager,
                img_wait=True,
                illu_wait=True,
            )
            self.create_stepset_acquisition(
                illusttg,
                imgsttg,
                unique_name=f"img-{round}",
                readable_name=imager,
                fluid_wait=True,
            )

            if not last_round:
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=washbuf,
                    washing=True,
                    unique_name=f"img-dark-{round}",
                    readable_name=imager,
                    img_wait=True,
                    illu_wait=True,
                )
                self.create_stepset_acquisition(
                    illusttg,
                    darkimgsttg,
                    unique_name=f"img-dark-{round}",
                    readable_name=imager,
                    fluid_wait=True,
                    name=f"{imager} (dark frames)",
                )
            elif last_round:
                if illusttg:
                    if illusttg["lasers_off_finally"]:
                        self.create_step_laserenable("all", False)
                        self.create_step_setshutter(state=False)

        return self.steps, self.reservoir_vols

    def create_steps_sph_resi(self, config):
        """In SPH-RESI, all target molecules of one target type are conjugated
        with the same sequence, and imaged in multiple rounds: for each round,
        a subset of the open target molecule sequences is labeled with an
        adapter, which has an imager docking sequence. After imaging that, the
        adapter is blocked using a blocker strand.
        (https://mibwiki.biochem.mpg.de/x/eoNZCg)

        As the number/density of open target molecule sequences decreases with
        time, different concentrations and incubation times are necessary for
        labeling equally-sized subsets.

        Parameters
        ----------
        config : dict
            Flow acquisition configuration with keys ``save_dir``,
            ``base_name``, ``fluid_settings``, ``imaging_settings``,
            ``illumination_settings`` and ``mm_parameters``.

        Returns
        -------
        steps : list of dict
            The aria steps.
        reservoir_vols : dict
            Maps reservoir names to volumes.

        Examples
        --------
        Example ``config``::

        config = {
            "fluid": {
                'parameters': {
                    'start_velocity': 50,
                    'max_velocity': 200,
                    'stop_velocity': 50,
                    'pumpout_dispense_velocity': 200,
                    'clean_velocity': 1500,
                    'mode': 'tubing_ignore',
                    # 'tubing_stack', 'tubing_flush' or 'tubing_ignore'
                    'extractionfactor': 4},
                'settings': {
                    'vol_wash': 7,  # in ul
                    'vol_reagent': 7,  # in ul
                    'reservoir_names': {
                        0: 'R1-lo', 1: 'R1-hi', 2: 'R3-A1', 3: 'R3-A2',
                        7: 'Blocker', 8: 'A1-c1', 9: 'A1-c2', 10: 'A1-c3',
                        11: 'A2-c1', 12: 'A2-c2',
                        18: 'Wash Buffer 1', 19: 'Wash Buffer 2'},
                    'experiment' : {
                        'type': 'SPH-RESI'
                        'wash_buffer_1': 'Wash Buffer 1',
                        'wash_buffer_2': None,  # skip if None
                        'blocker': 'Blocker',
                        'blocker_incubation': 5, # in minutes
                        'initial_imager_present': True,
                        'target-rounds': {  # keys: various targets
                            'A1': {  # general target parameters
                                'BC_imager_pre': 'R1-lo',
                                'frames_BC_pre': 5000,
                                'BC_imager_post': 'R1-hi',
                                'frames_BC_post': 15000,
                                'RESI-imager': 'R3-A1',
                                'RESI-frames': 50000,
                                'RESI-rounds': [  # params for each RESI round
                                    {
                                        'adapter': 'A1-c1',
                                        'adapter_incubation': .5,},
                                    {
                                        'adapter': 'A1-c1',
                                        'adapter_incubation': 5,},
                                    {
                                        'adapter': 'A1-c2',
                                        'adapter_incubation': .5,},
                                    {
                                        'adapter': 'A1-c2',
                                        'adapter_incubation': 15,},
                                    {
                                        'adapter': 'A1-c3',
                                        'adapter_incubation': 5,},
                                ],
                            },
                            'A2': {
                                'BC_imager_pre': 'R1-hi',
                                'frames_BC_pre': 5000,
                                'BC_imager_post': 'R1-hi',
                                'frames_BC_post': 15000,
                                'RESI-imager': 'R3-A2',
                                'RESI-frames': 50000,
                                'rounds': [
                                    {
                                        'adapter': 'A2-c1',
                                        'adapter_incubation': .5,},
                                    {
                                        'adapter': 'A2-c1',
                                        'adapter_incubation': 5,},
                                    {
                                        'adapter': 'A2-c2',
                                        'adapter_incubation': .5,},
                                ],
                            },
                        },
                    },
                }
            },
            "img": {
                'parameters': {
                    'show_progress': True,
                    'show_display': True,
                    'close_display_after_acquisition': True,
                    },
                'settings': {
                    'frames': 6,
                    'darkframes': 5,
                    't_exp': 100,  # in ms
                    }
            },
            "illu": {
                'parameters': {
                    'channel_group': 'Filter turret',
                    'filter': '2-G561',
                    'ROI': [512, 512, 512, 512]},
                'settings': {
                    'setup': 'Mercury',
                    'laser': 560,
                    'power': 30,  #mW
                    }
            }
        """
        experiment = config["fluid"]["settings"]["experiment"]
        reservoirs = config["fluid"]["settings"]["reservoir_names"]

        wait_after_pickup = config["fluid"]["settings"].get(
            "wait_after_pickup", 0
        )

        volumes = {
            "vol_remove_before_flush": config["fluid"]["settings"].get(
                "vol_remove_before_flush", 0
            ),
            "vol_reagent": config["fluid"]["settings"]["vol_reagent"],
            "vol_wash": config["fluid"]["settings"]["vol_wash"],
        }
        # volumes = {
        #     'vol_reduction': config['fluid']['settings'].get(
        #         'vol_remove_before_wash', 0),
        #     'wash': {
        #         'vol': config['fluid']['settings']['vol_wash'],
        #         'vol_pre': config['fluid']['settings']['vol_wash_pre'],
        #     },
        #     'reagent': {
        #         'vol': config['fluid']['settings']['vol_imager_post'],
        #         'vol_pre': config['fluid']['settings']['vol_imager_pre'],
        #     },
        # }

        initial_imager_present = experiment.get("initial_imager_present")

        general_imgsttg = config["img"]["settings"]
        illusttg = config.get("illu", {}).get("settings")

        # check that all mentioned sources acqually exist
        assert experiment["wash_buffer_1"] in reservoirs.values()

        res_idcs = {name: nr for nr, name in reservoirs.items()}

        # The wash buffers are defined in the experiment block (not repeated
        # in fluid.settings).
        washbuf1 = experiment["wash_buffer_1"]
        washbuf2 = experiment.get("wash_buffer_2")

        # self.create_step_pumpout(volume=volumes['wash_vol_pre'])
        # self.create_step_inject(
        #     volume=volumes['wash_vol_pre'], reservoir_id=res_idcs[washbuf1])

        # image round0 (e.g. for Alingment structures):
        if isinstance(experiment["round0"], dict):
            if not initial_imager_present:
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=experiment["round0"]["round0_imager"],
                    washing=False,
                    unique_name="Round0",
                    readable_name="Round0",
                    img_wait=True,
                    illu_wait=True,
                )

            imgsttg = {
                "t_exp": general_imgsttg["t_exp"],
                "frames": experiment["round0"]["frames_round0"],
            }
            self.create_stepset_acquisition(
                illusttg,
                imgsttg,
                unique_name="Round0",
                readable_name="Round0",
                fluid_wait=True,
                name="Round 0 (alignment)",
            )

            # wash using wash_buffer_1
            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=washbuf1,
                washing=True,
                img_wait=False,
                illu_wait=False,
            )

        # iterate over target rounds
        for tgt_round, (tgt, tgt_pars) in enumerate(
            experiment["target-rounds"].items()
        ):
            # target-round specific preparatory steps

            # inject barcode imager
            # if not initial_imager_present or tgt_round > 0:
            # only scenario where not injected: no round0 before +
            # initial imager (BC imager) precent + tgt_round = 0
            if not (
                (not isinstance(experiment["round0"], dict))
                and initial_imager_present
                and tgt_round == 0
            ):
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=tgt_pars["BC_imager_pre"],
                    washing=False,
                    unique_name=f"BC-pre_{tgt_round}",
                    readable_name=f"{tgt}",
                    img_wait=True,
                    illu_wait=True,
                )

            # image free barcodes (the DNA conjugated to the target):
            imgsttg = {
                "t_exp": general_imgsttg["t_exp"],
                "frames": tgt_pars["frames_BC_pre"],
            }
            self.create_stepset_acquisition(
                illusttg,
                imgsttg,
                unique_name=f"BC-pre_{tgt_round}",
                readable_name=f"{tgt}",
                fluid_wait=True,
                name=f"{tgt} barcode (pre)",
            )

            # wash using wash_buffer_1
            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=washbuf1,
                washing=True,
                img_wait=False,
                illu_wait=False,
            )

            #   wash with wash buffer 2: same volume as for wash buffer 1
            if washbuf2 is not None:
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=washbuf2,
                    washing=True,
                    img_wait=False,
                    illu_wait=False,
                )

            # iterate over resi-rounds for this target
            for resi_round, resi_pars in enumerate(tgt_pars["RESI-rounds"]):
                # resi-round specific steps

                # incubate adapter (mediating from barcode to docking strand)
                # with resi-round specific incubation parameters
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=resi_pars["adapter"],
                    washing=False,
                    t_incubate=resi_pars["adapter_incubation"],
                    img_wait=False,
                    illu_wait=False,
                )

                #   wash with wash buffer 2: same volume as for wash buffer 1
                if washbuf2 is not None:
                    self.create_stepset_flush(
                        volumes,
                        res_idcs,
                        wait_after_pickup,
                        reagent=washbuf2,
                        washing=True,
                        img_wait=False,
                        illu_wait=False,
                    )

                # wash using wash_buffer_1
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=washbuf1,
                    washing=True,
                    img_wait=False,
                    illu_wait=False,
                )

                # inject RESI imager
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=tgt_pars["RESI-imager"],
                    washing=False,
                    unique_name=f"resi_{tgt_round}-{resi_round}",
                    readable_name=f"{tgt}-{resi_round}",
                    img_wait=True,
                    illu_wait=True,
                )

                # perform RESI round imaging
                imgsttg = {
                    "t_exp": general_imgsttg["t_exp"],
                    "frames": tgt_pars["RESI-frames"],
                }
                self.create_stepset_acquisition(
                    illusttg,
                    imgsttg,
                    unique_name=f"resi_{tgt_round}-{resi_round}",
                    readable_name=f"{tgt}-{resi_round}",
                    fluid_wait=True,
                    name=f"{tgt} RESI round {resi_round + 1}",
                )

                # wash using wash_buffer_1
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=washbuf1,
                    washing=True,
                    img_wait=False,
                    illu_wait=False,
                )

                # wash with wash buffer 2: same volume as for wash buffer 1
                if washbuf2 is not None:
                    self.create_stepset_flush(
                        volumes,
                        res_idcs,
                        wait_after_pickup,
                        reagent=washbuf2,
                        washing=True,
                        img_wait=False,
                        illu_wait=False,
                    )

                # block the free adapters
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=experiment["blocker"],
                    washing=False,
                    t_incubate=experiment["blocker_incubation"],
                    img_wait=False,
                    illu_wait=False,
                )

                # wash with wash buffer 2: same volume as for wash buffer 1
                if washbuf2 is not None:
                    self.create_stepset_flush(
                        volumes,
                        res_idcs,
                        wait_after_pickup,
                        reagent=washbuf2,
                        washing=True,
                        img_wait=False,
                        illu_wait=False,
                    )
                # wash using wash_buffer_1
                else:
                    self.create_stepset_flush(
                        volumes,
                        res_idcs,
                        wait_after_pickup,
                        reagent=washbuf1,
                        washing=True,
                        img_wait=False,
                        illu_wait=False,
                    )
                    self.create_stepset_flush(
                        volumes,
                        res_idcs,
                        wait_after_pickup,
                        reagent=washbuf1,
                        washing=True,
                        img_wait=False,
                        illu_wait=False,
                    )

            # wash using wash_buffer_1
            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=washbuf1,
                washing=True,
                img_wait=False,
                illu_wait=False,
            )

            # post-resi target-specific steps

            # inject barcode imager
            self.create_stepset_flush(
                volumes,
                res_idcs,
                wait_after_pickup,
                reagent=tgt_pars["BC_imager_post"],
                washing=False,
                unique_name=f"BC-post_{tgt_round}",
                readable_name=f"{tgt}",
                img_wait=True,
                illu_wait=True,
            )
            # image free barcodes (the DNA conjugated to the target):
            imgsttg = {
                "t_exp": general_imgsttg["t_exp"],
                "frames": tgt_pars["frames_BC_post"],
            }
            self.create_stepset_acquisition(
                illusttg,
                imgsttg,
                unique_name=f"BC-post_{tgt_round}",
                readable_name=f"{tgt}",
                fluid_wait=True,
                name=f"{tgt} barcode (post)",
            )

            # if not last round:
            #   wash with wash buffer 1: 'vol_wash'
            if tgt_round < len(experiment["target-rounds"].keys()) - 1:
                # wash using wash_buffer_1
                self.create_stepset_flush(
                    volumes,
                    res_idcs,
                    wait_after_pickup,
                    reagent=washbuf1,
                    washing=True,
                    img_wait=False,
                    illu_wait=False,
                )

        # steps for finishing up.
        if illusttg:
            if illusttg["lasers_off_finally"]:
                self.create_step_laserenable("all", False)
                self.create_step_setshutter(state=False)

        return self.steps, self.reservoir_vols

    def create_steps_MERPAINT(self, config):
        """Create the protocol steps for a MERPAINT experiment.

        Parameters
        ----------
        experiment : dict
            The experiment configuration, with items:

            wash_buffer : str
                The name of the wash buffer reservoir (typically 2xSSC).
            wash_buffer_vol : float
                The wash volume in µl.
            hybridization_buffer : str
                The name of the hybridization buffer reservoir.
            hybridization_buffer_vol : float
                The hybridization buffer volume in µl.
            hybridization_time : float
                The hybridization incubation time in s.
            imaging_buffer : str
                The name of the imaging buffer reservoir (typically C+).
            imaging_buffer_vol : float
                The imaging buffer volume in µl.
            imagers : list
                The names of the imager reservoirs to use (for MERPAINT
                typically only 1).
            imager_vol : float
                The volume to flush imagers in µl.
            adapters : list
                The names of the secondary adapter reservoirs to use.
            adapter_vol : float
                The volume to flush adapters in µl.
            erasers : list
                The names of the eraser reservoirs to unzip the secondary
                adapters to use.
            eraser_vol : float
                The volume to flush erasers in µl.
            check_dark_frames : int, optional
                If present, add steps to check de-hybridization and image
                for said number of frames.
        reservoirs : dict
            Keys 1-10, values the names of the reservoirs.

        Returns
        -------
        steps : list of dict
            The aria steps.
        reservoir_vols : dict
            Maps reservoir names to volumes.
        """
        experiment = config["fluid"]["settings"]["experiment"]
        reservoirs = config["fluid"]["settings"]["reservoir_names"]
        # check that all mentioned sources acqually exist
        assert experiment["wash_buffer"] in reservoirs.values()
        assert experiment["hybridization_buffer"] in reservoirs.values()
        assert experiment["imaging_buffer"] in reservoirs.values()
        assert all(
            [name in reservoirs.values() for name in experiment["imagers"]]
        )
        assert all(
            [name in reservoirs.values() for name in experiment["adapters"]]
        )
        assert all(
            [name in reservoirs.values() for name in experiment["erasers"]]
        )

        imagers = experiment["imagers"]

        washbuf = experiment["wash_buffer"]
        hybbuf = experiment["hybridization_buffer"]
        imgbuf = experiment["imaging_buffer"]
        washvol = experiment["wash_buffer_vol"]
        hybvol = experiment["hybridization_buffer_vol"]
        imgbufvol = experiment["imaging_buffer_vol"]
        imagervol = experiment["imager_vol"]
        adaptervol = experiment["adapter_vol"]
        eraservol = experiment["adapter_vol"]
        hybtime = experiment["hybridization_time"]

        darkframes = experiment.get("check_dark_frames")
        if darkframes:
            check_dark_frames = True
        else:
            check_dark_frames = False
            darkframes = 0

        imgsttg = config["img"]["settings"]

        res_idcs = {name: nr for nr, name in reservoirs.items()}

        self.create_step_inject(10, res_idcs[washbuf])
        for merpaintround, (adapter, eraser) in enumerate(
            zip(experiment["adapters"], experiment["erasers"])
        ):
            # hybridization buffer
            self.create_step_inject(hybvol, res_idcs[hybbuf])
            # adapter
            self.create_step_inject(adaptervol, res_idcs[adapter])
            # incubation
            self.create_step_incubate(hybtime)
            # 2xSSC
            self.create_step_inject(washvol, res_idcs[washbuf])
            # iterate over imagers
            for imager_round, imager in enumerate(imagers):
                #   imaging buffer
                self.create_step_inject(imgbufvol, res_idcs[imgbuf])
                #   imager
                self.create_step_inject(imagervol, res_idcs[imager])
                # acquire movie, possibly in multiple rois
                sglmsg = "done fluids merpaint round {:d}".format(
                    merpaintround
                ) + ", imager round {:d}".format(imager_round)
                self.create_step_signal(system="fluid", message=sglmsg)
                self.create_step_waitfor_signal(
                    system="img", target="fluid", message=sglmsg
                )
                fname = "merpaintround{:d}".format(
                    merpaintround
                ) + "-imagerround{:d}".format(imager_round)
                self.create_step_acquire(
                    imgsttg["frames"], imgsttg["t_exp"], message=fname
                )
                sglmsg = "done imaging merpaint round {:d}".format(
                    merpaintround
                ) + ", imager round {:d}".format(imager_round)
                self.create_step_signal(system="img", message=sglmsg)
                self.create_step_waitfor_signal(
                    system="fluid", target="img", message=sglmsg
                )
            # de-hybridize adapter
            # washbuf
            self.create_step_inject(washvol, res_idcs[washbuf])
            # hybridization buffer
            self.create_step_inject(hybvol, res_idcs[hybbuf])
            # eraser
            self.create_step_inject(eraservol, res_idcs[eraser])
            # incubation
            self.create_step_incubate(hybtime)
            # washbuf
            self.create_step_inject(washvol, res_idcs[washbuf])
            if check_dark_frames:
                #   imaging buffer
                self.create_step_inject(imgbufvol, res_idcs[imgbuf])
                #   acquire movie
                sglmsg = "done darktest fluids round {:d}".format(round)
                self.create_step_signal(system="fluid", message=sglmsg)
                self.create_step_waitfor_signal(
                    system="img", target="fluid", message=sglmsg
                )
                fname = "darktest-merpaintround{:d}".format(
                    merpaintround
                ) + "-imagerround{:d}".format(imager_round)
                self.create_step_acquire(
                    imgsttg["darkframes"], imgsttg["t_exp"], message=fname
                )
                sglmsg = "done darktest imaging round {:d}".format(round)
                self.create_step_signal(system="img", message=sglmsg)
                self.create_step_waitfor_signal(
                    system="fluid", target="img", message=sglmsg
                )
                # washbuf
                self.create_step_inject(washvol, res_idcs[washbuf])

        return self.steps, self.reservoir_vols

    def create_steps_flushtest(self, config):
        """Create the protocol steps for testing flush volumes.

        Image acquisition is triggered both when washing and when flushing
        imagers.

        Parameters
        ----------
        experiment : dict
            The experiment configuration, with items:

            fluids : list of str
                The names of the imager or wash buffer reservoirs to use.
            fluid_vols : list of float
                The volumes of fluids to flush.
        reservoirs : dict
            Keys 1-10, values the names of the reservoirs.

        Returns
        -------
        steps : list of dict
            The aria steps.
        reservoir_vols : dict
            Maps reservoir names to volumes.
        imground_descriptions : list of str
            A description of each imaging round.
        """
        experiment = config["fluid_settings"]["experiment"]
        reservoirs = config["fluid_settings"]["reservoir_names"]
        assert all(
            [name in reservoirs.values() for name in experiment["fluids"]]
        )

        res_idcs = {name: nr - 1 for nr, name in reservoirs.items()}

        imgsttg = config["imaging_settings"]

        for round, (fluid, fluid_vol) in enumerate(
            zip(experiment["fluids"], experiment["fluid_vols"])
        ):
            # flush during acquisition
            self.create_step_inject(int(fluid_vol), res_idcs[fluid])
            fname = "flush-image-round{:d}".format(round)
            self.create_step_acquire(
                imgsttg["frames"], imgsttg["t_exp"], message=fname
            )
            # after flushing and acquiring, synchronize again
            sglmsg_i = "done imaging round {:d}".format(round)
            sglmsg_f = "done flushing round {:d}".format(round)
            self.create_step_signal(system="img", message=sglmsg_i)
            self.create_step_signal(system="fluid", message=sglmsg_f)
            self.create_step_waitfor_signal(
                system="img", target="fluid", message=sglmsg_f
            )
            self.create_step_waitfor_signal(
                system="fluid", target="img", message=sglmsg_i
            )

        return self.steps, self.reservoir_vols

    def create_step_incubate(self, t_incu):
        """Create a step to incubate for a given time.

        Parameters
        ----------
        t_incu : float
            The incubation time in minutes.

        Returns
        -------
        step : dict
            The step configuration.
        """
        timeout = t_incu * 60
        self.steps["fluid"].append({"$type": "incubate", "duration": timeout})

    def create_step_pumpout(self, volume, extractionfactor=None):
        """Create a step to pump out only.

        Parameters
        ----------
        volume : int
            Volume to pump out in integer µl.
        extractionfactor : float or None
            The extractionfactor to use, if different from default.

        Returns
        -------
        step : dict
            The step configuration.
        """
        volume = volume if volume > 0 else 1
        pars = {"$type": "pump_out", "volume": volume}
        if extractionfactor is not None:
            pars["extractionfactor"] = extractionfactor
        self.steps["fluid"].append(pars)

    def create_step_inject(
        self, volume, reservoir_id, delay=0, extractionfactor=None
    ):
        """Create a step to inject from a reservoir.

        Parameters
        ----------
        volume : int
            Volume to inject in integer µl.
        reservoir_id : int
            The reservoir to use.
        delay : int
            Number of seconds to wait between pickup and dispense.
        extractionfactor : float or None
            An extraction factor different to the default for this step.
            If None, the default is used.

        Returns
        -------
        step : dict
            The step configuration.
        """
        volume = volume if volume > 0 else 1
        self.steps["fluid"].append(
            {
                "$type": "inject",
                "volume": volume,
                "reservoir_id": reservoir_id,
                "delay": delay,
            }
        )
        if extractionfactor is not None:
            self.steps["fluid"][-1]["extractionfactor"] = extractionfactor
        self.reservoir_vols[reservoir_id] += volume

    def create_step_signal(self, system, message):
        self.steps[system].append({"$type": "signal", "value": message})

    def create_step_waitfor_signal(self, system, target, message):
        self.steps[system].append(
            {"$type": "wait for signal", "target": target, "value": message}
        )

    def create_step_acquire(self, nframes, t_exp, message, name=""):
        step = {
            "$type": "acquire",
            "frames": nframes,
            "t_exp": t_exp,
            "message": message,
        }
        if name:
            step["name"] = name
        self.steps["img"].append(step)

    def create_step_setpower(self, laser, power, warmup_delay=0, message=""):
        self.steps["illu"].append(
            {
                "$type": "set power",
                "laser": laser,
                "power": power,
                "warmup_delay": warmup_delay,
                "message": message,
            }
        )
        # message is not needed; only for finding entries to change
        # power in the yaml protocol. can be removed when manipulating
        # in GUI

    def create_step_setshutter(self, state):
        self.steps["illu"].append({"$type": "set shutter", "state": state})

    def create_step_laserenable(self, laser, state):
        """Switch laser(s) on or off.

        Parameters
        ----------
        laser : int or str
            An int for one laser (wavelength), or the str ``'all'``.
        state : bool
            On or off.
        """
        self.steps["illu"].append(
            {"$type": "laser enable", "laser": laser, "state": state}
        )
