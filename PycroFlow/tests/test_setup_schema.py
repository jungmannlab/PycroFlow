"""Hermetic tests for the setup schema + structured/passthrough split.

No Qt. The round-trip guarantee (``merge(split(x)) == x``) is the safety net
the GUI editor relies on to never lose or mangle a rig's reservoir wiring, so
it is asserted against every shipped and fixture setup.
"""

from __future__ import annotations

import unittest

from PycroFlow import configs
from PycroFlow.schemas.setup import (
    STRUCTURED_PATHS,
    SetupValidationError,
    merge_setup,
    split_setup,
    validate_setup_config,
)


def _all_setups():
    """(name, raw dict) for every discoverable setup (shipped + fixtures)."""
    for name in configs.list_setups():
        yield name, configs.load_setup_file(name)


class TestSplitMergeRoundTrip(unittest.TestCase):
    def test_every_setup_round_trips_exactly(self):
        seen = 0
        for name, raw in _all_setups():
            with self.subTest(setup=name):
                structured, passthrough = split_setup(raw)
                self.assertEqual(merge_setup(structured, passthrough), raw)
            seen += 1
        self.assertGreater(seen, 0, "no setups discovered to test")

    def test_wiring_stays_in_passthrough(self):
        # The reservoir valve wiring + multiplexer are the parts the form can't
        # edit safely; they must never land in the structured subtree.
        for name, raw in _all_setups():
            if "fluid" not in raw:
                continue
            with self.subTest(setup=name):
                structured, passthrough = split_setup(raw)
                pf = passthrough.get("fluid", {})
                self.assertIn("reservoirs", pf)
                self.assertIn("multiplexer", pf)
                sf = structured.get("fluid", {})
                self.assertIn("pumps", sf)
                self.assertNotIn("reservoirs", sf)
                self.assertNotIn("multiplexer", sf)

    def test_extras_preserved_in_passthrough(self):
        # camera_info / live_analysis / a top-level monitoring block must be
        # carried through untouched (not modeled by the structured form).
        raw = configs.load_setup_file("Ibidi")  # has camera_info + monitoring
        structured, passthrough = split_setup(raw)
        self.assertIn("monitoring", passthrough)
        self.assertIn("camera_info", passthrough["imaging"])
        self.assertIn("live_analysis", passthrough["imaging"])
        # ...and they are byte-identical after a round-trip.
        self.assertEqual(merge_setup(structured, passthrough), raw)

    def test_empty_block_is_preserved(self):
        # A block that is ALREADY empty on disk must survive the round-trip —
        # _prune_empty only drops containers it emptied by carving, not ones
        # that started empty.
        raw = configs.load_setup_file("Emulator")
        raw["imaging"] = {}  # degenerate, but must round-trip exactly
        structured, passthrough = split_setup(raw)
        self.assertEqual(passthrough.get("imaging"), {})
        self.assertEqual(merge_setup(structured, passthrough), raw)

    def test_merge_does_not_alias_inputs(self):
        # Mutating the merged result must not reach back into the structured or
        # passthrough dicts the caller passed in.
        raw = configs.load_setup_file("Emulator")
        structured, passthrough = split_setup(raw)
        merged = merge_setup(structured, passthrough)
        merged["fluid"]["pumps"]["interface"]["COM"] = "99"
        merged["fluid"]["reservoirs"].append({"id": 999})
        self.assertNotEqual(
            structured["fluid"]["pumps"]["interface"]["COM"], "99"
        )
        self.assertNotIn({"id": 999}, passthrough["fluid"]["reservoirs"])

    def test_structured_paths_are_disjoint_from_passthrough(self):
        raw = configs.load_setup_file("Mercury")
        structured, passthrough = split_setup(raw)
        for path in STRUCTURED_PATHS:
            # No structured path should survive in the passthrough tree.
            node = passthrough
            present = True
            for part in path:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    present = False
                    break
            self.assertFalse(
                present,
                "structured path {} leaked to passthrough".format(path),
            )


class TestValidate(unittest.TestCase):
    def test_accepts_every_setup(self):
        for name, raw in _all_setups():
            with self.subTest(setup=name):
                validate_setup_config(raw)  # must not raise

    def test_rejects_missing_required_field(self):
        raw = configs.load_setup_file("Emulator")
        del raw["fluid"]["pumps"]["interface"]["COM"]
        with self.assertRaises(SetupValidationError):
            validate_setup_config(raw)

    def test_unknown_blocks_are_allowed(self):
        raw = configs.load_setup_file("Emulator")
        raw["some_future_block"] = {"foo": 1}
        # extra='allow' -> unknown keys are retained, not rejected.
        model = validate_setup_config(raw)
        self.assertEqual(
            model.model_dump(by_alias=True)["some_future_block"], {"foo": 1}
        )


if __name__ == "__main__":
    unittest.main()
