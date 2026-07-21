"""Tests for the high-level experiment design: schema, setup configs,
builder split, and the ExperimentService translate path."""

import os
import tempfile
import unittest

import PycroFlow
from PycroFlow import configs
from PycroFlow.protocols import ProtocolBuilder
from PycroFlow.schemas import (
    validate_experiment_design,
    ExperimentDesignValidationError,
)
from PycroFlow.services import ExperimentService

_EXAMPLE = os.path.join(
    os.path.dirname(PycroFlow.__file__), "examples", "sph_resi_6plex.yaml"
)


def _example_design():
    svc = ExperimentService()
    return svc.load_experiment_design(_EXAMPLE)


class TestExperimentDesignSchema(unittest.TestCase):

    def test_accepts_example_sphresi(self):
        design = _example_design()
        model = validate_experiment_design(design)
        self.assertEqual(model.fluid.settings.experiment.type, "SPH-RESI")

    def test_fields_declare_units(self):
        from PycroFlow.schemas.experiment_design import (
            field_unit,
            FluidParameters,
            FluidSettings,
            ImgSettings,
            IlluSettings,
            ResiRound,
        )

        def u(model, field):
            return field_unit(model.model_fields[field])

        self.assertEqual(u(FluidParameters, "max_velocity"), "µl/min")
        self.assertEqual(u(FluidParameters, "clean_delay"), "s")
        self.assertEqual(u(FluidParameters, "inject_in_to_out_delay"), "s")
        self.assertEqual(u(FluidSettings, "vol_wash"), "µl")
        self.assertEqual(u(ResiRound, "adapter_incubation"), "min")
        self.assertEqual(u(ImgSettings, "t_exp"), "ms")
        self.assertEqual(u(IlluSettings, "power_acq"), "mW")
        self.assertEqual(u(IlluSettings, "warmup_delay"), "s")
        # Unitless fields report None.
        self.assertIsNone(u(FluidParameters, "extractionfactor"))

    def test_fluid_settings_reordered_and_wash_buffers_removed(self):
        from PycroFlow.schemas.experiment_design import FluidSettings

        fields = list(FluidSettings.model_fields)
        # Wash buffers live only in the experiment block now.
        self.assertNotIn("wash_buffer_1", fields)
        self.assertNotIn("wash_buffer_2", fields)
        # Reservoir tables first; volumes then cleaning above experiment.
        self.assertEqual(fields[:2], ["reservoir_names", "special_names"])
        self.assertEqual(fields[-2:], ["cleaning_reservoirs", "experiment"])

    def test_illu_section_has_no_parameters(self):
        from PycroFlow.schemas.experiment_design import IlluSection

        # The illu 'parameters' block is gone — the monet config name comes
        # from the microscope setup, not the design.
        self.assertNotIn("parameters", IlluSection.model_fields)
        # The example (no longer carrying illu.parameters) still validates.
        model = validate_experiment_design(_example_design())
        self.assertEqual(model.illu.settings.laser, 642)

    def test_units_do_not_break_validation(self):
        # json_schema_extra is metadata only — the example still validates and
        # round-trips through model_dump(by_alias=True).
        model = validate_experiment_design(_example_design())
        self.assertEqual(model.fluid.parameters.max_velocity, 10000)

    def test_accepts_exchange(self):
        design = {
            "base_name": "x",
            "fluid": {
                "settings": {
                    "vol_wash": 10,
                    "vol_imager_post": 5,
                    "reservoir_names": {1: "R1"},
                    "experiment": {
                        "type": "Exchange",
                        "wash_buffer": "B",
                        "imagers": ["R1"],
                    },
                }
            },
            "img": {"settings": {"t_exp": 100, "frames": 100}},
        }
        model = validate_experiment_design(design)
        self.assertEqual(model.fluid.settings.experiment.type, "Exchange")

    def test_rejects_bad_experiment_type(self):
        design = _example_design()
        design["fluid"]["settings"]["experiment"]["type"] = "NOPE"
        with self.assertRaises(ExperimentDesignValidationError):
            validate_experiment_design(design)

    def test_rejects_missing_required(self):
        design = _example_design()
        del design["fluid"]["settings"]["vol_wash"]
        with self.assertRaises(ExperimentDesignValidationError):
            validate_experiment_design(design)

    def test_hyphenated_aliases_round_trip(self):
        model = validate_experiment_design(_example_design())
        d = model.model_dump(by_alias=True)
        exp = d["fluid"]["settings"]["experiment"]
        self.assertIn("target-rounds", exp)
        tr = exp["target-rounds"]["A1"]
        self.assertIn("RESI-rounds", tr)
        self.assertIn("RESI-imager", tr)

    def test_power_nonacq_defaults_to_acq(self):
        model = validate_experiment_design(
            {
                "base_name": "x",
                "fluid": {
                    "settings": {
                        "vol_wash": 1,
                        "reservoir_names": {1: "R1"},
                        "experiment": {
                            "type": "Exchange",
                            "wash_buffer": "B",
                            "imagers": ["R1"],
                        },
                    }
                },
                "img": {"settings": {"t_exp": 100}},
                "illu": {"settings": {"laser": 642, "power_acq": 70}},
            }
        )
        self.assertEqual(model.illu.settings.power_nonacq, 70)


class TestSetupConfigs(unittest.TestCase):

    def test_list_setups(self):
        setups = configs.list_setups()
        self.assertIn("Emulator", setups)
        self.assertIn("Mercury", setups)

    def test_load_setup_tubing_tuple_keys(self):
        setup = configs.load_setup("Mercury")
        self.assertFalse(setup["emulated"])
        # tubing records convert to a tuple-keyed dict
        self.assertIn(("pump_a", "sample"), setup["tubing"])

    def test_assemble_filters_and_attaches(self):
        setup = configs.load_setup("Emulator")
        ham, tub = configs.assemble_hamilton_config(
            setup,
            {
                "reservoir_names": {1: "A1", 7: "C+"},
                "special_names": {"flushbuffer_a": 7, "h2o": 16},
                "cleaning_reservoirs": ["h2o"],
            },
        )
        ids = sorted(r["id"] for r in ham["reservoir_a"])
        self.assertEqual(ids, [1, 7, 16])  # 16 pulled in via cleaning 'h2o'
        self.assertEqual(ham["special_names"]["flushbuffer_a"], 7)
        self.assertEqual(ham["cleaning_reservoirs"], ["h2o"])
        self.assertIn("interface", ham)

    def test_assemble_unknown_reservoir_raises(self):
        setup = configs.load_setup("Emulator")
        with self.assertRaises(KeyError):
            configs.assemble_hamilton_config(
                setup, {"reservoir_names": {999: "nope"}}
            )


class TestBuilderSplit(unittest.TestCase):

    def test_build_protocol_no_io(self):
        design = _example_design()
        before = set(os.listdir("."))
        protocol = ProtocolBuilder().build_protocol(design)
        after = set(os.listdir("."))
        self.assertEqual(before, after)  # nothing written
        self.assertIn("fluid", protocol)
        self.assertGreater(len(protocol["fluid"]["protocol_entries"]), 0)

    def test_create_protocol_writes(self):
        design = _example_design()
        with tempfile.TemporaryDirectory() as d:
            design["save_dir"] = d
            fname, steps = ProtocolBuilder().create_protocol(design)
            self.assertTrue(os.path.exists(os.path.join(d, fname)))
            self.assertIn("fluid", steps)


class TestEmulatedFluidOps(unittest.TestCase):

    def _connected(self):
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("Emulator")
        svc.connect_fluid(
            {
                "parameters": {
                    "max_velocity": 200,
                    "clean_velocity": 200,
                    "clean_delay": 0,
                },
                "settings": {
                    "reservoir_names": {1: "R1", 7: "C+"},
                    "special_names": {"flushbuffer_a": 7, "h2o": 16},
                    "cleaning_reservoirs": ["h2o"],
                },
            }
        )
        return svc

    def test_fill_tubings_runs(self):
        svc = self._connected()
        svc.fill_tubings()  # over the fake serial; must not raise

    def test_clean_tubings_is_gui_safe(self):
        from unittest import mock

        svc = self._connected()
        # No terminal prompt: even with input() sabotaged, clean runs.
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input() must not be called"),
        ):
            svc.clean_tubings()

    def test_clean_tubings_without_reservoirs_raises(self):
        # An empty/unresolved cleaning_reservoirs would pump nothing — clean
        # must raise (so the GUI reports it) instead of silently completing.
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("Emulator")
        svc.connect_fluid(
            {
                "parameters": {"max_velocity": 200, "clean_velocity": 200},
                "settings": {
                    "reservoir_names": {1: "R1", 7: "C+"},
                    "special_names": {"flushbuffer_a": 7},
                    "cleaning_reservoirs": [],
                },
            }
        )
        with self.assertRaises(ValueError):
            svc.clean_tubings()

    def test_disconnect_releases_systems(self):
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("Emulator")
        svc.connect_fluid(
            {
                "parameters": {"max_velocity": 200},
                "settings": {
                    "reservoir_names": {1: "R1"},
                    "special_names": {"flushbuffer_a": 1},
                },
            }
        )
        svc.connect_imaging()
        svc.connect_illumination()
        self.assertEqual(
            svc.connection_states(),
            {"fluid": True, "imaging": True, "illumination": True},
        )
        svc.disconnect_all()
        self.assertEqual(
            svc.connection_states(),
            {"fluid": False, "imaging": False, "illumination": False},
        )
        # Idempotent: disconnecting again is a safe no-op.
        svc.disconnect_all()
        # And the hardware is free to reconnect afterwards.
        svc.connect_fluid(
            {
                "parameters": {"max_velocity": 200},
                "settings": {
                    "reservoir_names": {1: "R1"},
                    "special_names": {"flushbuffer_a": 1},
                },
            }
        )
        self.assertTrue(svc.connection_states()["fluid"])

    def test_inject_step_duration_estimate(self):
        fluid = self._connected().fluid_system
        # inject of 1000 µl at 200 µl/min -> ~2*1000/200 = 10 min = 600 s.
        est = fluid._estimate_entry_duration(
            {"$type": "inject", "volume": 1000}
        )
        self.assertGreaterEqual(est, 600)
        # non-time-based steps have no estimate.
        self.assertIsNone(
            fluid._estimate_entry_duration({"$type": "signal", "value": "x"})
        )

    def test_get_step_progress_during_inject(self):
        import time

        fluid = self._connected().fluid_system
        # Idle: nothing running.
        self.assertIsNone(fluid.get_step_progress())
        # Simulate a running inject started 1 s ago with a 100 s estimate.
        fluid._step_estimate = (time.time() - 1.0, 100.0, "inject")
        cur, tot, label = fluid.get_step_progress()
        self.assertEqual((tot, label), (100.0, "inject"))
        self.assertTrue(0.5 <= cur <= 3.0)
        # Elapsed is capped at the estimate.
        fluid._step_estimate = (time.time() - 500.0, 100.0, "inject")
        self.assertEqual(fluid.get_step_progress()[0], 100.0)


class TestTranslate(unittest.TestCase):

    def test_translate_from_design(self):
        svc = ExperimentService()
        svc.load_experiment_design(_EXAMPLE)
        protocol = svc.translate()
        self.assertEqual(svc.state.value, "loaded")
        self.assertGreater(len(protocol["fluid"]["protocol_entries"]), 0)
        self.assertGreater(len(protocol["img"]["protocol_entries"]), 0)
        self.assertGreater(len(protocol["illu"]["protocol_entries"]), 0)

    def test_attach_systems_feeds_translate(self):
        from unittest.mock import MagicMock

        svc = ExperimentService()
        fluid = MagicMock(name="fluid")
        svc.attach_systems(fluid_system=fluid)
        svc.load_experiment_design(_EXAMPLE)
        svc.translate()
        self.assertIs(svc.orchestrator.fluid_system, fluid)

    def test_load_from_path_changes_cwd(self):
        import shutil

        original = os.getcwd()
        self.addCleanup(os.chdir, original)
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        dst = os.path.join(folder, "design.yaml")
        shutil.copy(_EXAMPLE, dst)
        ExperimentService().load_experiment_design(dst)
        self.assertEqual(
            os.path.realpath(os.getcwd()), os.path.realpath(folder)
        )

    def test_load_from_dict_keeps_cwd(self):
        original = os.getcwd()
        self.addCleanup(os.chdir, original)
        ExperimentService().load_experiment_design(_example_design())
        self.assertEqual(os.getcwd(), original)


class TestSubsystemDeselection(unittest.TestCase):
    """A subsystem can be deselected in the design (``enabled: false``) so it
    is left out of the compiled Run Sequence without dangling waits."""

    def _entries(self, protocol, system):
        return protocol.get(system, {}).get("protocol_entries", [])

    def _wait_targets(self, protocol):
        targets = set()
        for system in protocol.values():
            for entry in system["protocol_entries"]:
                if entry.get("$type") == "wait for signal":
                    targets.add(entry["target"])
        return targets

    def test_enabled_defaults_true_and_round_trips(self):
        model = validate_experiment_design(_example_design())
        self.assertTrue(model.fluid.enabled)
        self.assertTrue(model.img.enabled)
        self.assertTrue(model.illu.enabled)
        d = model.model_dump(by_alias=True)
        self.assertTrue(d["illu"]["enabled"])

    def test_all_enabled_matches_baseline(self):
        # Adding the flag must not change the emitted protocol when everything
        # is enabled (the default) — same subsystems, same waits.
        protocol = ProtocolBuilder().build_protocol(_example_design())
        self.assertEqual(set(protocol.keys()), {"fluid", "img", "illu"})

    def test_deselect_illu_drops_key_and_waits(self):
        design = _example_design()
        design["illu"]["enabled"] = False
        protocol = ProtocolBuilder().build_protocol(design)
        self.assertNotIn("illu", protocol)
        # fluid + img still present and non-empty (structure intact).
        self.assertTrue(self._entries(protocol, "fluid"))
        self.assertTrue(self._entries(protocol, "img"))
        # No survivor waits on the dropped illu subsystem.
        self.assertNotIn("illu", self._wait_targets(protocol))

    def test_deselect_img_drops_key_and_orphan_fluid_waits(self):
        design = _example_design()
        design["img"]["enabled"] = False
        protocol = ProtocolBuilder().build_protocol(design)
        self.assertNotIn("img", protocol)
        self.assertTrue(self._entries(protocol, "fluid"))
        # The fluid 'wait for signal target=img' (done imaging) is pruned;
        # likewise any illu wait on img.
        self.assertNotIn("img", self._wait_targets(protocol))

    def test_design_without_illu_block_compiles(self):
        # An absent illu section (illu -> None after validation) must not
        # crash the exchange builder; it just yields a fluid+img protocol.
        design = {
            "base_name": "x",
            "fluid": {
                "settings": {
                    "vol_wash": 10,
                    "vol_imager_post": 5,
                    "reservoir_names": {1: "R1"},
                    "experiment": {
                        "type": "Exchange",
                        "wash_buffer": "R1",
                        "imagers": ["R1"],
                    },
                }
            },
            "img": {"settings": {"t_exp": 100, "frames": 100}},
        }
        design = ExperimentService().load_experiment_design(design)
        protocol = ProtocolBuilder().build_protocol(design)
        self.assertEqual(set(protocol.keys()), {"fluid", "img"})

    def test_deselected_subsystem_not_wired_into_orchestrator(self):
        from unittest.mock import MagicMock

        design = _example_design()
        design["illu"]["enabled"] = False
        svc = ExperimentService()
        svc.attach_systems(
            fluid_system=MagicMock(),
            imaging_system=MagicMock(),
            illumination_system=MagicMock(),
        )
        svc.load_experiment_design(design)
        svc.translate()
        # The illu handler must not receive a system (its protocol key is
        # absent), otherwise it would crash on an empty entry list mid-run.
        self.assertIsNone(svc._orchestrator.illumination_system)
        self.assertIsNotNone(svc._orchestrator.fluid_system)
        self.assertIsNotNone(svc._orchestrator.imaging_system)


if __name__ == "__main__":
    unittest.main()
