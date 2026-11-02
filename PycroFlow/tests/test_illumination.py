"""Tests for IlluminationSystem against a fake monet instrument.

monet itself is mocked at import (tests/_mock_hardware), so we don't exercise
``_load_monet_control``; instead we inject a hand-built fake ``instrument`` with
the attribute surface IlluminationSystem touches and verify the control logic
(laser selection, power, attenuation, beam path, protocol dispatch, pause/abort).
"""

import unittest
from unittest.mock import MagicMock

from PycroFlow.illumination import IlluminationSystem


class FakeAttenuator:
    def __init__(self):
        self.wavelength = None
        self.pos = None
        self.homed = False

    def set_wavelength(self, wl):
        self.wavelength = wl

    def set(self, pos):
        self.pos = pos

    def home(self):
        self.homed = True
        self.pos = "home"

    def curr_pos(self):
        return self.pos


class FakeLaser:
    def __init__(self):
        self.enabled = False


class FakeBeampath:
    def __init__(self):
        self.positions = None
        self.objects = {"shutter": MagicMock(autoshutter=True)}


class FakeInstrument:
    """Mirrors the bit of monet's IlluminationLaserControl that
    IlluminationSystem touches. Assigning ``laser`` updates ``curr_laser``
    (monet's property does this), which downstream methods like
    ``beampath_open`` read."""

    def __init__(self):
        self._curr = 488
        self.curr_laser = 488
        self.lasers = {488: FakeLaser(), 561: FakeLaser()}
        self.attenuator = FakeAttenuator()
        self.beampath = FakeBeampath()
        self.power = 0
        self.laserpower = 0

    @property
    def laser(self):
        return self._curr

    @laser.setter
    def laser(self, value):
        self._curr = value
        self.curr_laser = value


def _make_system():
    isy = IlluminationSystem()
    isy.instrument = FakeInstrument()
    isy.power_setvalues = {488: 10, 561: 20}
    isy.mprotocol = {
        "beampath": {488: ["open488"], 561: ["open561"], "end": ["closed"]},
    }
    return isy


class LaserControlTest(unittest.TestCase):
    def test_set_laser_switches_wavelength_and_enables(self):
        isy = _make_system()
        isy.set_laser(561)
        self.assertEqual(isy.instrument.laser, 561)
        self.assertEqual(isy.instrument.attenuator.wavelength, 561)
        self.assertTrue(isy.instrument.lasers[561].enabled)

    def test_set_laser_restores_saved_power(self):
        # Regression: set_laser called a nonexistent self.do_power(); it now
        # restores the saved sample power for the newly selected laser
        # (power_setvalues[561] == 20).
        isy = _make_system()
        isy.instrument.power = 0
        isy.set_laser(561)
        self.assertEqual(isy.instrument.power, 20)

    def test_set_laser_enabled_toggles(self):
        isy = _make_system()
        isy.set_laser_enabled(488, True)
        self.assertTrue(isy.instrument.lasers[488].enabled)
        isy.set_laser_enabled(488, False)
        self.assertFalse(isy.instrument.lasers[488].enabled)

    def test_set_laser_power(self):
        isy = _make_system()
        isy.set_laser_power(42)
        self.assertEqual(isy.instrument.laserpower, 42)

    def test_set_sample_power_updates_and_records(self):
        isy = _make_system()
        isy.set_sample_power(75)
        self.assertEqual(isy.instrument.power, 75)
        self.assertEqual(isy.power_setvalues[isy.instrument.curr_laser], 75)

    def test_set_sample_power_noop_when_unchanged(self):
        isy = _make_system()
        isy.instrument.power = 30
        isy.set_sample_power(30)  # equal -> no change, no sleep
        self.assertEqual(isy.instrument.power, 30)


class AttenuationTest(unittest.TestCase):
    def test_set_attenuation_numeric(self):
        isy = _make_system()
        isy.set_attenuation("0.5")
        self.assertEqual(isy.instrument.attenuator.pos, 0.5)

    def test_set_attenuation_home(self):
        isy = _make_system()
        isy.set_attenuation("HOME")
        self.assertTrue(isy.instrument.attenuator.homed)


class BeampathTest(unittest.TestCase):
    def test_beampath_open_sets_positions_for_current_laser(self):
        isy = _make_system()
        isy.beampath_open()
        self.assertEqual(isy.instrument.beampath.positions, ["open488"])

    def test_beampath_close_sets_end_positions(self):
        isy = _make_system()
        isy.beampath_close()
        self.assertEqual(isy.instrument.beampath.positions, ["closed"])

    def test_beampath_open_guards_missing_protocol(self):
        isy = _make_system()
        isy.mprotocol = None  # triggers the guarded warning path, no raise
        isy.beampath_open()
        isy.beampath_close()


class ProtocolDispatchTest(unittest.TestCase):
    def _run(self, entries):
        isy = _make_system()
        isy.protocol = {"protocol_entries": entries}
        for i in range(len(entries)):
            isy.execute_protocol_entry(i)
        return isy

    def test_set_power_entry(self):
        isy = self._run(
            [
                {"$type": "set power", "laser": 561, "power": 55},
            ]
        )
        self.assertEqual(isy.instrument.laser, 561)
        self.assertEqual(isy.instrument.power, 55)
        self.assertEqual(isy.instrument.beampath.positions, ["open561"])

    def test_set_shutter_entry_open_and_close(self):
        isy = self._run(
            [
                {"$type": "set shutter", "state": True},
            ]
        )
        self.assertEqual(isy.instrument.beampath.positions, ["open488"])
        isy.protocol = {
            "protocol_entries": [{"$type": "set shutter", "state": False}]
        }
        isy.execute_protocol_entry(0)
        self.assertEqual(isy.instrument.beampath.positions, ["closed"])

    def test_laser_enable_entry_single(self):
        isy = self._run(
            [
                {"$type": "laser enable", "laser": 561, "state": True},
            ]
        )
        self.assertTrue(isy.instrument.lasers[561].enabled)

    def test_laser_enable_entry_all(self):
        isy = self._run(
            [
                {"$type": "laser enable", "laser": "all", "state": True},
            ]
        )
        self.assertTrue(
            all(laser.enabled for laser in isy.instrument.lasers.values())
        )


class LazyMonetTest(unittest.TestCase):
    def test_assign_protocol_does_not_load_monet(self):
        # _assign_protocol must not open the lasers — so translating a design
        # (which builds the orchestrator) never touches hardware.
        isy = IlluminationSystem()
        isy._assign_protocol(
            {"protocol_entries": [], "parameters": {"setup": "X"}}
        )
        self.assertIsNone(getattr(isy, "instrument", None))

    def test_ensure_monet_loads_once(self):
        isy = IlluminationSystem()
        isy._assign_protocol(
            {"protocol_entries": [], "parameters": {"setup": "X"}}
        )
        calls = []

        def fake_load(name):
            calls.append(name)
            isy.instrument = FakeInstrument()

        isy._load_monet_control = fake_load
        isy._ensure_monet()
        self.assertEqual(calls, ["X"])
        isy._ensure_monet()  # idempotent — already loaded
        self.assertEqual(calls, ["X"])


class PauseAbortTest(unittest.TestCase):
    def test_pause_resume_abort_flags(self):
        isy = _make_system()
        isy.pause_execution()
        self.assertTrue(isy._paused)
        self.assertTrue(isy.resume_execution())
        self.assertFalse(isy._paused)
        isy.pause_execution()
        isy.abort_execution()
        self.assertFalse(isy._paused)


class AllOffFailSafeTest(unittest.TestCase):
    """The T3 fail-safe: honest shutter report + zero-laser escalation."""

    def test_beampath_close_returns_true_on_success(self):
        isy = _make_system()
        self.assertTrue(isy.beampath_close())
        self.assertEqual(isy.instrument.beampath.positions, ["closed"])

    def test_beampath_close_returns_false_when_unactuatable(self):
        isy = _make_system()
        isy.mprotocol = {"beampath": {}}  # no "end" -> KeyError
        self.assertFalse(isy.beampath_close())

    def test_all_off_disables_all_and_reports_shutter_closed(self):
        isy = _make_system()
        for la in isy.instrument.lasers.values():
            la.enabled = True
        report = isy.all_off()
        self.assertEqual(sorted(report["disabled"]), [488, 561])
        self.assertEqual(report["failed"], [])
        self.assertTrue(report["shutter_closed"])
        self.assertTrue(
            all(not la.enabled for la in isy.instrument.lasers.values())
        )

    def test_all_off_reports_shutter_not_closed_when_beampath_fails(self):
        # The false-"shutter_closed=True" bug: the shutter can't actuate but
        # lasers are reachable -> the report must say shutter_closed=False.
        isy = _make_system()
        isy.mprotocol = {"beampath": {}}  # beampath_close -> False
        report = isy.all_off()
        self.assertEqual(sorted(report["disabled"]), [488, 561])
        self.assertFalse(report["shutter_closed"])
        self.assertTrue(
            any("shutter" in e for e in report["errors"]), report["errors"]
        )

    def test_all_off_flags_a_laser_that_fails_to_disable(self):
        # A laser that raises on disable goes to `failed` (still potentially
        # emitting) — the others are still disabled.
        isy = _make_system()
        for la in isy.instrument.lasers.values():
            la.enabled = True

        class _Unresponsive:
            @property
            def enabled(self):
                return True

            @enabled.setter
            def enabled(self, value):
                raise RuntimeError("laser unresponsive")

        isy.instrument.lasers[561] = _Unresponsive()
        report = isy.all_off()
        self.assertIn(488, report["disabled"])
        self.assertEqual(report["failed"], [561])
        self.assertFalse(isy.instrument.lasers[488].enabled)

    def test_all_off_with_no_instrument_darkens_nothing_and_flags_unsafe(self):
        # The overnight-abort case: monet never built (dead beampath device),
        # so no laser is reachable. all_off must NOT raise, must report zero
        # disabled + shutter not closed, and record why.
        isy = IlluminationSystem()  # no instrument, no monet setup
        report = isy.all_off()
        self.assertEqual(report["disabled"], [])
        self.assertFalse(report["shutter_closed"])
        self.assertTrue(
            any("zero lasers" in e for e in report["errors"]), report["errors"]
        )


if __name__ == "__main__":
    unittest.main()
