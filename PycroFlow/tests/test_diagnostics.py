"""Tests for the frontend-agnostic DiagnosticsService (Doctor checks)."""

import types
import unittest
from unittest import mock

import urllib.error

from PycroFlow.services import ExperimentState
from PycroFlow.services.diagnostics import (
    CheckStatus,
    DiagnosticsService,
    summarize,
)


class _Dev:
    """Minimal pump/valve stub exposing the ``get_status`` ping."""

    def __init__(self, reply):
        self._reply = reply

    def get_status(self):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


class _FakeFluid:
    def __init__(self):
        self.multiplexer = _Dev("MX;")
        self.pump_a = _Dev("ok-a")
        self.pump_out = _Dev("ok-out")
        # 'ibidi' aliases the multiplexer (must not be double-counted);
        # valve 'A' never answers -> FAIL.
        self.valve_a = {"ibidi": self.multiplexer, "A": _Dev("")}


class _FakeSys:
    def __init__(
        self,
        fluid=None,
        imaging=None,
        illumination=None,
        setup="Emulator",
        emulated=True,
        lasers=None,
        monet_setup=None,
    ):
        self.fluid_system = fluid
        self.imaging_system = imaging
        self.illumination_system = illumination
        self._setup = setup
        self._emulated = emulated
        self._lasers = lasers or []
        self._monet_setup = monet_setup

    def connection_states(self):
        return {
            "fluid": self.fluid_system is not None,
            "imaging": self.imaging_system is not None,
            "illumination": self.illumination_system is not None,
        }

    def setup_name(self):
        return self._setup

    def is_emulated(self):
        return self._emulated

    def laser_options(self):
        return self._lasers

    def get_monet_setup(self):
        return self._monet_setup


class _FakeExp:
    def __init__(self, state=ExperimentState.IDLE):
        self.state = state


def _by_name(results):
    return {r.name: r for r in results}


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        # Keep every test independent of the real environment's registry vars.
        self._env = mock.patch.dict(
            "os.environ",
            {"PAINT_REGISTRY_URL": "", "PAINT_REGISTRY_TOKEN": ""},
            clear=False,
        )
        self._env.start()
        import os

        os.environ.pop("PAINT_REGISTRY_URL", None)
        os.environ.pop("PAINT_REGISTRY_TOKEN", None)

    def tearDown(self):
        self._env.stop()

    def test_run_all_has_three_categories(self):
        results = DiagnosticsService(_FakeSys()).run_all()
        cats = {r.category for r in results}
        self.assertEqual(cats, {"Environment", "Subsystems", "Connectors"})

    def test_disconnected_subsystems_warn_and_skip_pings(self):
        results = DiagnosticsService(_FakeSys()).run_all()
        rows = _by_name(results)
        self.assertEqual(rows["Fluid"].status, CheckStatus.WARN)
        self.assertEqual(rows["Imaging"].status, CheckStatus.WARN)
        self.assertEqual(rows["Illumination"].status, CheckStatus.WARN)
        # No device pings when nothing is connected.
        self.assertNotIn("Fluid · pump_a", rows)

    def test_setup_ok_with_emulated_note(self):
        results = DiagnosticsService(_FakeSys(setup="Mercury")).run_all()
        setup = _by_name(results)["Setup"]
        self.assertEqual(setup.status, CheckStatus.OK)
        self.assertIn("Mercury", setup.detail)
        self.assertIn("emulated", setup.detail)

    def test_setup_warn_when_none(self):
        results = DiagnosticsService(_FakeSys(setup=None)).run_all()
        self.assertEqual(_by_name(results)["Setup"].status, CheckStatus.WARN)

    def test_fluid_device_pings(self):
        svc = DiagnosticsService(_FakeSys(fluid=_FakeFluid()))
        rows = _by_name(svc.run_all())
        self.assertEqual(rows["Fluid · pump_a"].status, CheckStatus.OK)
        self.assertEqual(rows["Fluid · pump_out"].status, CheckStatus.OK)
        self.assertEqual(
            rows["Fluid · ibidi multiplexer"].status, CheckStatus.OK
        )
        # Non-answering valve -> FAIL; multiplexer not pinged twice.
        self.assertEqual(rows["Fluid · valve A"].status, CheckStatus.FAIL)
        self.assertNotIn("Fluid · valve ibidi", rows)

    def test_device_ping_exception_is_fail_not_raise(self):
        fluid = _FakeFluid()
        fluid.pump_a = _Dev(RuntimeError("boom"))
        rows = _by_name(DiagnosticsService(_FakeSys(fluid=fluid)).run_all())
        self.assertEqual(rows["Fluid · pump_a"].status, CheckStatus.FAIL)

    def test_pings_skipped_during_a_run(self):
        svc = DiagnosticsService(
            _FakeSys(fluid=_FakeFluid(), imaging=object()),
            _FakeExp(ExperimentState.RUNNING),
        )
        rows = _by_name(svc.run_all())
        self.assertEqual(rows["Fluid · devices"].status, CheckStatus.SKIP)
        self.assertEqual(rows["Imaging · MM Core"].status, CheckStatus.SKIP)
        # The cached connection rows still show.
        self.assertEqual(rows["Fluid"].status, CheckStatus.OK)
        self.assertNotIn("Fluid · pump_a", rows)

    def test_lasers_ok_when_present(self):
        svc = DiagnosticsService(
            _FakeSys(illumination=object(), lasers=[488, 561])
        )
        rows = _by_name(svc.run_all())
        self.assertEqual(rows["Illumination · lasers"].status, CheckStatus.OK)
        self.assertIn("488", rows["Illumination · lasers"].detail)

    def test_registry_skipped_when_unconfigured(self):
        rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(rows["Registry · client"].status, CheckStatus.SKIP)
        self.assertEqual(
            rows["Registry · reachability"].status, CheckStatus.SKIP
        )

    def test_registry_reachable_ok(self):
        import os

        os.environ["PAINT_REGISTRY_URL"] = "http://registry.local"
        resp = mock.MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with mock.patch(
            "PycroFlow.services.diagnostics.urllib.request.urlopen",
            return_value=resp,
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(
            rows["Registry · reachability"].status, CheckStatus.OK
        )

    def test_registry_auth_rejected_warns(self):
        import os

        os.environ["PAINT_REGISTRY_URL"] = "http://registry.local"
        err = urllib.error.HTTPError(
            "http://registry.local", 401, "Unauthorized", None, None
        )
        with mock.patch(
            "PycroFlow.services.diagnostics.urllib.request.urlopen",
            side_effect=err,
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        row = rows["Registry · reachability"]
        self.assertEqual(row.status, CheckStatus.WARN)
        self.assertIn("auth rejected", row.detail)

    def test_registry_unreachable_fails(self):
        import os

        os.environ["PAINT_REGISTRY_URL"] = "http://registry.local"
        with mock.patch(
            "PycroFlow.services.diagnostics.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(
            rows["Registry · reachability"].status, CheckStatus.FAIL
        )

    def test_registry_token_never_in_detail(self):
        import os

        secret = "SUPER-SECRET-TOKEN-xyz"
        os.environ["PAINT_REGISTRY_URL"] = "http://registry.local"
        os.environ["PAINT_REGISTRY_TOKEN"] = secret
        resp = mock.MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with mock.patch(
            "PycroFlow.services.diagnostics.urllib.request.urlopen",
            return_value=resp,
        ):
            results = DiagnosticsService(_FakeSys()).run_all()
        for r in results:
            self.assertNotIn(secret, r.detail)

    def test_monet_configs_loaded_ok(self):
        fake = types.SimpleNamespace(
            CONFIGS={"Mercury": {}}, PROTOCOLS={"Mercury": {}}
        )
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(rows["monet · library"].status, CheckStatus.OK)
        self.assertEqual(rows["monet · configs"].status, CheckStatus.OK)
        self.assertIn("1 config", rows["monet · configs"].detail)

    def test_monet_configs_without_protocols_warn(self):
        fake = types.SimpleNamespace(CONFIGS={"Mercury": {}}, PROTOCOLS={})
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(rows["monet · configs"].status, CheckStatus.WARN)

    def test_monet_not_installed_warns_and_skips(self):
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=None
        ):
            rows = _by_name(DiagnosticsService(_FakeSys()).run_all())
        self.assertEqual(rows["monet · library"].status, CheckStatus.WARN)
        self.assertEqual(rows["monet · configs"].status, CheckStatus.SKIP)

    def test_monet_microscope_skipped_without_setup(self):
        fake = types.SimpleNamespace(CONFIGS={"Mercury": {}}, PROTOCOLS={})
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(
                DiagnosticsService(_FakeSys(monet_setup=None)).run_all()
            )
        self.assertEqual(rows["monet · microscope"].status, CheckStatus.SKIP)

    def test_monet_microscope_found(self):
        fake = types.SimpleNamespace(
            CONFIGS={"Mercury": {}}, PROTOCOLS={"Mercury": {}}
        )
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(
                DiagnosticsService(_FakeSys(monet_setup="Mercury")).run_all()
            )
        row = rows["monet · microscope"]
        self.assertEqual(row.status, CheckStatus.OK)
        self.assertIn("Mercury", row.detail)

    def test_monet_microscope_config_without_protocol_warns(self):
        fake = types.SimpleNamespace(CONFIGS={"Mercury": {}}, PROTOCOLS={})
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(
                DiagnosticsService(_FakeSys(monet_setup="Mercury")).run_all()
            )
        row = rows["monet · microscope"]
        self.assertEqual(row.status, CheckStatus.WARN)
        self.assertIn("no protocol", row.detail)

    def test_monet_microscope_not_found_fails(self):
        fake = types.SimpleNamespace(
            CONFIGS={"Mercury": {}}, PROTOCOLS={"Mercury": {}}
        )
        with mock.patch.object(
            DiagnosticsService, "_import_monet", return_value=fake
        ):
            rows = _by_name(
                DiagnosticsService(_FakeSys(monet_setup="Venus")).run_all()
            )
        row = rows["monet · microscope"]
        self.assertEqual(row.status, CheckStatus.FAIL)
        self.assertIn("Venus", row.detail)
        self.assertIn("Mercury", row.detail)  # lists what is available

    def test_summarize_counts(self):
        results = DiagnosticsService(_FakeSys(fluid=_FakeFluid())).run_all()
        counts = summarize(results)
        self.assertEqual(sum(counts.values()), len(results))
        self.assertGreaterEqual(counts[CheckStatus.OK], 1)


if __name__ == "__main__":
    unittest.main()
