"""Tests for the plain-English Run Sequence narration
(:mod:`PycroFlow.protocols.describe`)."""

import unittest

import PycroFlow.tests  # noqa: F401  (installs hardware mocks)
from PycroFlow.protocols import ProtocolBuilder
from PycroFlow.protocols.describe import describe_entry, describe_protocol


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

    def test_merges_adjacent_same_reservoir_injects(self):
        # The builder's main + tiny helper injects for one imager collapse to
        # a single line (100 + 1 µl -> 101 µl).
        _names, protocol = _exchange_protocol()
        lines = describe_protocol(
            protocol, {1: "Imager 1", 2: "Imager 2", 3: "Buffer"}
        )
        self.assertTrue(any("101 µl of Imager 1" in ln for ln in lines))
        # No two consecutive lines both inject Imager 1 (they were merged).
        self.assertEqual(sum("of Imager 1 into" in ln for ln in lines), 1)

    def test_empty_protocol_is_empty_list(self):
        self.assertEqual(describe_protocol({}), [])


if __name__ == "__main__":
    unittest.main()
