"""Tests for the Run Sequence duration estimates
(:mod:`PycroFlow.protocols.timing`)."""

import os
import unittest

import PycroFlow
from PycroFlow.protocols import ProtocolBuilder
from PycroFlow.protocols.timing import (
    estimate_entry_duration,
    estimate_durations,
    estimate_total_duration,
    estimate_remaining,
    format_duration,
)
from PycroFlow.schemas import validate_experiment_design

_EXAMPLE = os.path.join(
    os.path.dirname(PycroFlow.__file__), "examples", "sph_resi_6plex.yaml"
)


def _example_protocol():
    import yaml

    with open(_EXAMPLE) as f:
        design = validate_experiment_design(yaml.safe_load(f)).model_dump(
            by_alias=True
        )
    return ProtocolBuilder().build_protocol(design)


class TestEntryDuration(unittest.TestCase):

    def test_acquire_is_frames_times_exposure_plus_overhead(self):
        # 1000 frames * (100 ms exposure + 90 ms readout) + 2 s arm/startup.
        d = estimate_entry_duration(
            {"$type": "acquire", "frames": 1000, "t_exp": 100}
        )
        self.assertAlmostEqual(d, 1000 * (0.1 + 0.09) + 2.0)

    def test_acquire_overheads_are_overridable(self):
        # est_frame_overhead / est_acquire_setup tune the acquire model.
        d = estimate_entry_duration(
            {"$type": "acquire", "frames": 100, "t_exp": 100},
            {"est_frame_overhead": 0.0, "est_acquire_setup": 0.0},
        )
        self.assertAlmostEqual(d, 10.0)  # back to frames * t_exp

    def test_zero_frame_acquire_is_zero(self):
        self.assertEqual(
            estimate_entry_duration({"$type": "acquire", "frames": 0}), 0.0
        )

    def test_incubate_uses_duration(self):
        self.assertAlmostEqual(
            estimate_entry_duration({"$type": "incubate", "duration": 42}),
            42.0,
        )
        # Strings are accepted (orchestration coerces with float()).
        self.assertAlmostEqual(
            estimate_entry_duration({"$type": "incubate", "duration": "12"}),
            12.0,
        )

    def test_inject_uses_volume_over_velocity_plus_overhead(self):
        # 120 * 500 / 1000 = 60 s motion, plus the fixed inject overhead.
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500, "velocity": 1000}
        )
        self.assertAlmostEqual(d, 60.0 + 3.45)

    def test_inject_falls_back_to_max_velocity(self):
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500}, {"max_velocity": 1000}
        )
        self.assertAlmostEqual(d, 60.0 + 3.45)

    def test_inject_overhead_dominates_small_volumes(self):
        # A 1 µl inject is almost all fixed overhead (the log's 1 µl injects
        # took ~3 s though motion is ~0.01 s).
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 1, "velocity": 10000}
        )
        self.assertAlmostEqual(d, 120.0 / 10000 + 3.45)

    def test_inject_overhead_is_overridable(self):
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500, "velocity": 1000},
            {"est_inject_overhead": 0.0},
        )
        self.assertAlmostEqual(d, 60.0)

    def test_pump_out_adds_its_own_overhead(self):
        d = estimate_entry_duration(
            {"$type": "pump_out", "volume": 1, "velocity": 10000}
        )
        self.assertAlmostEqual(d, 120.0 / 10000 + 1.6)

    def test_inject_adds_equilibration_delays(self):
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500, "velocity": 1000, "delay": 5},
            {"inject_in_to_out_delay": 3, "inject_out_to_in_delay": 2},
        )
        self.assertAlmostEqual(d, 60.0 + 3 + 2 + 2 * 5 + 3.45)

    def test_coordination_and_instant_steps_are_zero(self):
        for entry in (
            {"$type": "signal", "value": "x"},
            {"$type": "wait for signal", "target": "img", "value": "x"},
            {"$type": "set power", "laser": 1, "power": 10},
            {"$type": "flush", "flushfactor": 1},
        ):
            self.assertEqual(estimate_entry_duration(entry), 0.0)

    def test_missing_params_are_zero_not_error(self):
        self.assertEqual(estimate_entry_duration({"$type": "inject"}), 0.0)
        self.assertEqual(estimate_entry_duration(None), 0.0)


class TestProtocolTotals(unittest.TestCase):

    def test_durations_align_with_entries(self):
        proto = _example_protocol()
        durs = estimate_durations(proto)
        for system in ("fluid", "img", "illu"):
            self.assertIn(system, durs)
            self.assertEqual(
                len(durs[system]), len(proto[system]["protocol_entries"])
            )

    def test_total_is_positive_and_sums_subsystems(self):
        proto = _example_protocol()
        durs = estimate_durations(proto)
        total = estimate_total_duration(proto)
        self.assertGreater(total, 0)
        self.assertAlmostEqual(total, sum(sum(v) for v in durs.values()))

    def test_remaining_decreases_as_steps_complete(self):
        durs = estimate_durations(_example_protocol())
        at_start = {s: (0, len(v)) for s, v in durs.items()}
        at_end = {s: (len(v), len(v)) for s, v in durs.items()}
        self.assertGreater(estimate_remaining(durs, at_start), 0)
        self.assertAlmostEqual(estimate_remaining(durs, at_end), 0.0)
        self.assertGreaterEqual(
            estimate_remaining(durs, at_start),
            estimate_remaining(durs, at_end),
        )

    def test_empty_protocol_is_zero(self):
        self.assertEqual(estimate_total_duration({}), 0)
        self.assertEqual(estimate_durations(None), {})


class TestFormatDuration(unittest.TestCase):

    def test_formats(self):
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(-5), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(90), "1m")
        self.assertEqual(format_duration(3700), "1h 1m")
        self.assertEqual(format_duration(90000), "1d 1h")


class TestVolumes(unittest.TestCase):

    def test_sums_injects_per_reservoir_and_waste(self):
        from PycroFlow.protocols.timing import estimate_volumes

        protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 100},
                    {"$type": "inject", "reservoir_id": 1, "volume": 50},
                    {"$type": "inject", "reservoir_id": 2, "volume": 500},
                    {"$type": "pump_out", "volume": 600},
                    {"$type": "signal", "value": "x"},  # ignored
                ]
            }
        }
        vols = estimate_volumes(protocol)
        self.assertEqual(vols["per_reservoir"], {1: 150.0, 2: 500.0})
        self.assertEqual(vols["total_injected"], 650.0)
        # Waste = simultaneous extraction of every inject (ef defaults to 1)
        # plus the standalone pump_out: 100 + 50 + 500 + 600.
        self.assertEqual(vols["total_waste"], 1250.0)

    def test_waste_scales_with_extraction_factor(self):
        # Each inject extracts extractionfactor * volume; a per-entry factor
        # (e.g. the 0 re-inject that pushes liquid back) overrides the default.
        from PycroFlow.protocols.timing import estimate_volumes

        protocol = {
            "fluid": {
                "parameters": {"extractionfactor": 6},
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 100},
                    {"$type": "inject", "reservoir_id": 1, "volume": 10,
                     "extractionfactor": 0},
                    {"$type": "pump_out", "volume": 5, "extractionfactor": 1},
                ],
            }
        }
        vols = estimate_volumes(protocol)
        self.assertEqual(vols["total_injected"], 110.0)
        # 6*100 (default ef) + 0*10 (per-entry) + 1*5 (per-entry) = 605.
        self.assertEqual(vols["total_waste"], 605.0)

    def test_empty_protocol_is_zero(self):
        from PycroFlow.protocols.timing import estimate_volumes

        vols = estimate_volumes({})
        self.assertEqual(vols["per_reservoir"], {})
        self.assertEqual(vols["total_injected"], 0.0)

    def test_example_protocol_has_positive_volume(self):
        from PycroFlow.protocols.timing import estimate_volumes

        vols = estimate_volumes(_example_protocol())
        self.assertGreater(vols["total_injected"], 0)

    def test_format_volume(self):
        from PycroFlow.protocols.timing import format_volume

        self.assertEqual(format_volume(0), "0 µl")
        self.assertEqual(format_volume(-5), "0 µl")
        self.assertEqual(format_volume(750), "750 µl")
        self.assertEqual(format_volume(1000), "1 ml")
        self.assertEqual(format_volume(2500), "2.50 ml")


if __name__ == "__main__":
    unittest.main()
