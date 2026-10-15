"""Tests for protocol-schema runnability checks (check_runnable).

check_runnable is the consistency / no-hang gate for hand-edited Run
Sequences: it flags waits whose signal is never emitted (orphan waits) and
circular cross-subsystem waits (deadlocks), mirroring the orchestrator's
own (target, value) signal matching including the legacy "<target> <value>"
prefix convention.
"""

import unittest

from PycroFlow.schemas import check_runnable
from PycroFlow.protocols import ProtocolBuilder
import PycroFlow.tests.fixtures.configs.exchange_basic as exchange_basic


class TestCheckRunnable(unittest.TestCase):

    def test_real_exchange_protocol_is_runnable(self):
        proto = ProtocolBuilder().build_protocol(exchange_basic.CONFIG)
        self.assertEqual(check_runnable(proto), [])

    def test_matched_signal_is_runnable(self):
        proto = {
            "fluid": {
                "protocol_entries": [{"$type": "signal", "value": "a"}]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "fluid",
                     "value": "a"}
                ]
            },
        }
        self.assertEqual(check_runnable(proto), [])

    def test_legacy_prefixed_signal_matches(self):
        # The orchestrator fires "<target> <value>" under the stripped value
        # too; check_runnable mirrors that so legacy protocols are not falsely
        # flagged.
        proto = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "signal", "value": "fluid round 1 done"}
                ]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "fluid",
                     "value": "round 1 done"}
                ]
            },
        }
        self.assertEqual(check_runnable(proto), [])

    def test_orphan_wait_flagged(self):
        proto = {
            "img": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "fluid",
                     "value": "never"}
                ]
            }
        }
        issues = check_runnable(proto)
        self.assertEqual(len(issues), 1)
        self.assertIn("never", issues[0])
        self.assertIn("img step 0", issues[0])

    def test_wrong_target_flagged(self):
        # The value is emitted, but by the wrong subsystem.
        proto = {
            "illu": {
                "protocol_entries": [{"$type": "signal", "value": "go"}]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "fluid",
                     "value": "go"}
                ]
            },
        }
        issues = check_runnable(proto)
        self.assertTrue(issues)
        self.assertIn("'fluid'", issues[0])

    def test_circular_wait_deadlock_flagged(self):
        # fluid waits on img's signal before emitting its own; img waits on
        # fluid's signal before emitting its own -> neither can start.
        proto = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "img",
                     "value": "i"},
                    {"$type": "signal", "value": "f"},
                ]
            },
            "img": {
                "protocol_entries": [
                    {"$type": "wait for signal", "target": "fluid",
                     "value": "f"},
                    {"$type": "signal", "value": "i"},
                ]
            },
        }
        issues = check_runnable(proto)
        self.assertTrue(any("Circular wait" in i for i in issues))

    def test_empty_protocol_is_runnable(self):
        self.assertEqual(check_runnable({}), [])
        self.assertEqual(check_runnable(None), [])


if __name__ == "__main__":
    unittest.main()
