"""Snapshot regression for ProtocolBuilder.create_steps().

Loads every fixture under tests/fixtures/configs/, runs create_steps, and
compares the result to a committed JSON snapshot under
tests/fixtures/snapshots/. Any change in the step list is a wire-format
change that must be reviewed deliberately.

To regenerate snapshots after an intended change:

    PYCROFLOW_UPDATE_SNAPSHOTS=1 python -m unittest \
        PycroFlow.tests.test_regression_protocols -v

The new snapshots will be written; commit them with the wire-format change.

This test is the safety net for Stage 3 (protocols.py split) and Stage 4
(typed protocol entries) — both refactors must leave the produced steps
byte-identical for existing experiments.
"""

import importlib
import json
import os
import pkgutil
import unittest
from pathlib import Path

import PycroFlow.protocols as pprot
from PycroFlow.tests.fixtures import configs as configs_pkg

_FIXTURES_ROOT = Path(__file__).parent / "fixtures"
_SNAPSHOTS_DIR = _FIXTURES_ROOT / "snapshots"
_UPDATE = os.environ.get("PYCROFLOW_UPDATE_SNAPSHOTS") == "1"


def _discover_fixtures():
    """Yield (fixture_name, config_dict) for each fixture module."""
    for module_info in pkgutil.iter_modules(configs_pkg.__path__):
        name = module_info.name
        mod = importlib.import_module(f"{configs_pkg.__name__}.{name}")
        config = getattr(mod, "CONFIG", None)
        if config is None:
            continue
        yield name, config


def _normalize(obj):
    """Round-trip through JSON so int keys, tuples, etc. normalize the way the
    on-disk snapshot would. The snapshot is the source of truth — anything
    that doesn't survive JSON serialization is not a stable wire artifact."""
    return json.loads(json.dumps(obj, sort_keys=True, default=str))


class TestRegressionProtocols(unittest.TestCase):
    """One assertion per fixture; emit a clear diff on mismatch."""

    def test_create_steps_snapshots(self):
        fixtures = list(_discover_fixtures())
        if not fixtures:
            self.skipTest("no fixtures under tests/fixtures/configs/")

        _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        failures = []

        for name, config in fixtures:
            with self.subTest(fixture=name):
                builder = pprot.ProtocolBuilder()
                steps, reservoir_vols = builder.create_steps(config)
                actual = _normalize(
                    {
                        "steps": steps,
                        "reservoir_vols": reservoir_vols,
                    }
                )

                snapshot_path = _SNAPSHOTS_DIR / f"{name}.json"

                if _UPDATE or not snapshot_path.exists():
                    snapshot_path.write_text(
                        json.dumps(
                            actual, indent=2, sort_keys=True, default=str
                        )
                    )
                    if not _UPDATE:
                        self.skipTest(
                            f"wrote initial snapshot for {name!r}; "
                            f"commit {snapshot_path.relative_to(_FIXTURES_ROOT.parent)} "
                            f"and re-run."
                        )
                    continue

                expected = json.loads(snapshot_path.read_text())
                if actual != expected:
                    failures.append((name, snapshot_path, expected, actual))

        if failures:
            msg_lines = ["snapshot mismatch:"]
            for name, path, expected, actual in failures:
                msg_lines.append(f"  fixture {name!r} differs from {path}")
                msg_lines.append(
                    "    regenerate with PYCROFLOW_UPDATE_SNAPSHOTS=1 if change is intentional."
                )
            self.fail("\n".join(msg_lines))


if __name__ == "__main__":
    unittest.main()
