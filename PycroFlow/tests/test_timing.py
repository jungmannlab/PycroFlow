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

    def test_acquire_is_frames_times_exposure(self):
        # 1000 frames * 100 ms = 100 s.
        d = estimate_entry_duration(
            {"$type": "acquire", "frames": 1000, "t_exp": 100}
        )
        self.assertAlmostEqual(d, 100.0)

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

    def test_inject_uses_volume_over_velocity(self):
        # 120 * 500 / 1000 = 60 s, plus delays.
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500, "velocity": 1000}
        )
        self.assertAlmostEqual(d, 60.0)

    def test_inject_falls_back_to_max_velocity(self):
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500}, {"max_velocity": 1000}
        )
        self.assertAlmostEqual(d, 60.0)

    def test_inject_adds_equilibration_delays(self):
        d = estimate_entry_duration(
            {"$type": "inject", "volume": 500, "velocity": 1000, "delay": 5},
            {"inject_in_to_out_delay": 3, "inject_out_to_in_delay": 2},
        )
        self.assertAlmostEqual(d, 60.0 + 3 + 2 + 2 * 5)

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


if __name__ == "__main__":
    unittest.main()
