"""Coverage for the SPH-RESI experiment builder (create_steps_sph_resi),
including dispatch through the EXPERIMENT_TYPES registry and the round0 /
wash-buffer-2 branches. Illumination is omitted (illusttg=None) so the
acquisition stepset takes its no-illumination path."""

import unittest

import PycroFlow.protocols as pprot
from PycroFlow.tests import TEST_OUTPUT_DIR


def _img_settings():
    return {
        "parameters": {},
        "settings": {"frames": 50000, "darkframes": 50, "t_exp": 100},
    }


def _base_config(experiment, reservoir_names):
    return {
        "save_dir": TEST_OUTPUT_DIR,
        "base_name": "sph_resi_test",
        "fluid": {
            "parameters": {},
            "settings": {
                "vol_wash": 7,
                "vol_reagent": 7,
                "vol_remove_before_flush": 2,
                "wait_after_pickup": 0,
                "reservoir_names": reservoir_names,
                "wash_buffer_1": experiment["wash_buffer_1"],
                "wash_buffer_2": experiment.get("wash_buffer_2"),
                "experiment": experiment,
            },
        },
        "img": _img_settings(),
        # No 'illu' key -> illusttg is None -> acquisition skips laser steps.
    }


class SphResiBuilderTest(unittest.TestCase):
    def _build(self, config):
        pb = pprot.ProtocolBuilder()
        pb.reservoir_vols = {
            i: 0 for i in config["fluid"]["settings"]["reservoir_names"]
        }
        pb.steps = {"fluid": [], "img": [], "illu": []}
        return pb

    def test_round0_disabled_single_target(self):
        reservoir_names = {
            0: "R1-lo",
            1: "R1-hi",
            2: "R3-A1",
            7: "Blocker",
            8: "A1-c1",
            9: "A1-c2",
            18: "Wash Buffer 1",
        }
        experiment = {
            "type": "SPH-RESI",
            "wash_buffer_1": "Wash Buffer 1",
            "wash_buffer_2": None,
            "blocker": "Blocker",
            "blocker_incubation": 5,
            "initial_imager_present": False,  # -> BC_imager_pre injection runs
            "round0": False,  # not a dict -> round0 block skipped
            "target-rounds": {
                "A1": {
                    "BC_imager_pre": "R1-lo",
                    "frames_BC_pre": 5000,
                    "BC_imager_post": "R1-hi",
                    "frames_BC_post": 15000,
                    "RESI-imager": "R3-A1",
                    "RESI-frames": 50000,
                    "RESI-rounds": [
                        {"adapter": "A1-c1", "adapter_incubation": 0.5},
                        {"adapter": "A1-c2", "adapter_incubation": 5},
                    ],
                },
            },
        }
        config = _base_config(experiment, reservoir_names)
        pb = self._build(config)
        pb.create_steps_sph_resi(config)
        self.assertGreater(len(pb.steps["fluid"]), 0)
        self.assertGreater(len(pb.steps["img"]), 0)

    def test_round0_dict_and_wash_buffer_2_two_targets(self):
        reservoir_names = {
            0: "R1-lo",
            1: "R1-hi",
            2: "R3-A1",
            3: "R3-A2",
            7: "Blocker",
            8: "A1-c1",
            9: "A1-c2",
            11: "A2-c1",
            18: "Wash Buffer 1",
            19: "Wash Buffer 2",
        }
        experiment = {
            "type": "SPH-RESI",
            "wash_buffer_1": "Wash Buffer 1",
            "wash_buffer_2": "Wash Buffer 2",  # exercises washbuf2 branches
            "blocker": "Blocker",
            "blocker_incubation": 5,
            "initial_imager_present": False,
            "round0": {  # dict -> round0 block runs
                "round0_imager": "R1-lo",
                "frames_round0": 1000,
            },
            "target-rounds": {
                "A1": {
                    "BC_imager_pre": "R1-lo",
                    "frames_BC_pre": 5000,
                    "BC_imager_post": "R1-hi",
                    "frames_BC_post": 15000,
                    "RESI-imager": "R3-A1",
                    "RESI-frames": 50000,
                    "RESI-rounds": [
                        {"adapter": "A1-c1", "adapter_incubation": 0.5}
                    ],
                },
                "A2": {  # second target -> "not last round" wash
                    "BC_imager_pre": "R1-hi",
                    "frames_BC_pre": 5000,
                    "BC_imager_post": "R1-hi",
                    "frames_BC_post": 15000,
                    "RESI-imager": "R3-A2",
                    "RESI-frames": 50000,
                    "RESI-rounds": [
                        {"adapter": "A2-c1", "adapter_incubation": 5}
                    ],
                },
            },
        }
        config = _base_config(experiment, reservoir_names)
        pb = self._build(config)
        pb.create_steps_sph_resi(config)
        self.assertGreater(len(pb.steps["fluid"]), 0)
        self.assertGreater(len(pb.steps["img"]), 0)

    def test_dispatch_via_create_steps_registry(self):
        # Exercises EXPERIMENT_TYPES dispatch for the 'sph-resi' key.
        reservoir_names = {
            0: "R1-lo",
            1: "R1-hi",
            2: "R3-A1",
            7: "Blocker",
            8: "A1-c1",
            18: "Wash Buffer 1",
        }
        experiment = {
            "type": "SPH-RESI",
            "wash_buffer_1": "Wash Buffer 1",
            "wash_buffer_2": None,
            "blocker": "Blocker",
            "blocker_incubation": 5,
            "initial_imager_present": True,
            "round0": False,
            "target-rounds": {
                "A1": {
                    "BC_imager_pre": "R1-lo",
                    "frames_BC_pre": 5000,
                    "BC_imager_post": "R1-hi",
                    "frames_BC_post": 15000,
                    "RESI-imager": "R3-A1",
                    "RESI-frames": 50000,
                    "RESI-rounds": [
                        {"adapter": "A1-c1", "adapter_incubation": 0.5}
                    ],
                },
            },
        }
        config = _base_config(experiment, reservoir_names)
        pb = pprot.ProtocolBuilder()
        steps, reservoir_vols = pb.create_steps(config)
        self.assertIn("fluid", steps)
        self.assertGreater(len(steps["fluid"]), 0)

    def test_unknown_experiment_type_raises(self):
        config = _base_config(
            {"type": "Nonexistent", "wash_buffer_1": "Wash Buffer 1"},
            {18: "Wash Buffer 1"},
        )
        pb = pprot.ProtocolBuilder()
        with self.assertRaises(KeyError):
            pb.create_steps(config)


if __name__ == "__main__":
    unittest.main()
