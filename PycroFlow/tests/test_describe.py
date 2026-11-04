"""Tests for the plain-English Run Sequence narration
(:mod:`PycroFlow.protocols.describe`)."""

import unittest

import PycroFlow.tests  # noqa: F401  (installs hardware mocks)
from PycroFlow.protocols import ProtocolBuilder
from PycroFlow.protocols.describe import (
    action_label,
    describe_entry,
    describe_protocol,
)


def _exchange_protocol():
    design = {
        "base_name": "x",
        "save_dir": ".",
        "fluid": {
            "settings": {
                "reservoir_names": {1: "Imager 1", 2: "Imager 2", 3: "Buffer"},
                "vol_wash": 500,
                "vol_reagent": 100,
                "experiment": {
                    "type": "Exchange",
                    "wash_buffer": "Buffer",
                    "imagers": ["Imager 1", "Imager 2"],
                },
            }
        },
        "img": {"settings": {"t_exp": 100, "frames": 30000}},
    }
    return design, ProtocolBuilder().build_protocol(design)


class TestActionLabel(unittest.TestCase):

    def test_compact_labels_for_the_status_line(self):
        self.assertEqual(
            action_label(
                {"$type": "inject", "reservoir_id": 1}, {1: "Imager 1"}
            ),
            "inject Imager 1",
        )
        self.assertEqual(
            action_label({"$type": "inject", "reservoir_id": 7}, {}),
            "inject reservoir 7",
        )
        self.assertEqual(
            action_label({"$type": "acquire", "name": "EGFR"}), "acquire EGFR"
        )
        self.assertEqual(action_label({"$type": "pump_out"}), "extract")
        self.assertEqual(action_label({"$type": "wait for signal"}), "wait")
        self.assertEqual(action_label({"$type": "signal"}), "sync")
        self.assertEqual(action_label({"$type": "incubate"}), "incubate")

    def test_unknown_type_falls_back_to_raw(self):
        self.assertEqual(action_label({"$type": "mystery"}), "mystery")
        self.assertEqual(action_label("not a dict"), "—")


class TestDescribeEntry(unittest.TestCase):

    def test_inject_names_the_reservoir(self):
        line = describe_entry(
            {"$type": "inject", "reservoir_id": 1, "volume": 100},
            {1: "Imager 1"},
        )
        self.assertEqual(line, "Pump 100 µl of Imager 1 into the sample")

    def test_inject_falls_back_to_id(self):
        line = describe_entry(
            {"$type": "inject", "reservoir_id": 7, "volume": 50}, {}
        )
        self.assertEqual(line, "Pump 50 µl of reservoir 7 into the sample")

    def test_pump_out_and_acquire_and_incubate(self):
        self.assertEqual(
            describe_entry({"$type": "pump_out", "volume": 600}),
            "Extract 600 µl from the sample",
        )
        self.assertEqual(
            describe_entry(
                {"$type": "acquire", "frames": 30000, "t_exp": 100}
            ),
            "Acquire 30000 frames (100 ms each)",
        )
        self.assertEqual(
            describe_entry({"$type": "incubate", "duration": 120}),
            "Incubate for 2m",
        )

    def test_coordination_steps_have_no_narration(self):
        self.assertIsNone(describe_entry({"$type": "signal", "value": "x"}))
        self.assertIsNone(
            describe_entry({"$type": "wait for signal", "value": "x"})
        )


class TestDescribeProtocol(unittest.TestCase):

    def test_orders_fluid_and_imaging_by_happens_before(self):
        _names, protocol = _exchange_protocol()
        lines = describe_protocol(
            protocol, {1: "Imager 1", 2: "Imager 2", 3: "Buffer"}
        )
        # Imager 1 injection precedes its acquisition, which precedes the
        # buffer wash and the Imager 2 round.
        joined = "\n".join(lines)
        i_img1 = joined.index("Imager 1")
        i_acq = joined.index("Acquire")
        i_buffer = joined.index("Buffer")
        i_img2 = joined.index("Imager 2")
        self.assertLess(i_img1, i_acq)
        self.assertLess(i_acq, i_buffer)
        self.assertLess(i_buffer, i_img2)

    def test_single_pre_inject_line_per_imager(self):
        # One clean pre-imaging inject line per imager (no 1 µl artefacts).
        _names, protocol = _exchange_protocol()
        lines = describe_protocol(
            protocol, {1: "Imager 1", 2: "Imager 2", 3: "Buffer"}
        )
        self.assertTrue(any("100 µl of Imager 1" in ln for ln in lines))
        self.assertEqual(sum("of Imager 1 into" in ln for ln in lines), 1)

    def test_coalesces_adjacent_same_reservoir_injects(self):
        # When the builder does emit adjacent same-reservoir injects, they
        # merge into one line summing the volumes (100 + 1 -> 101).
        protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 1, "volume": 100},
                    {"$type": "inject", "reservoir_id": 1, "volume": 1},
                ]
            }
        }
        lines = describe_protocol(protocol, {1: "Imager 1"})
        self.assertEqual(lines, ["Pump 101 µl of Imager 1 into the sample"])

    def test_empty_protocol_is_empty_list(self):
        self.assertEqual(describe_protocol({}), [])


if __name__ == "__main__":
    unittest.main()
