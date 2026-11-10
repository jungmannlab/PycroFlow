"""Tests for the ibidi MultiFlOW 24-channel multiplexer driver.

The real :class:`PycroFlow.ibidi_multiplexer.IbidiMultiplexer` runs against
the serial-level :class:`FakeIbidiSerial` so the command encode / response
decode path gets genuine coverage, plus an integration test that builds a
``LegacyArchitecture`` from the ``IbidiEmulator`` setup and routes reservoirs.
"""

import threading
import unittest

import PycroFlow.pyHamilton as ham
from PycroFlow.hal import Valve as ValveABC
from PycroFlow.ibidi_multiplexer import IbidiMultiplexer
from PycroFlow.tests import emulators as emu


class IbidiMultiplexerDriverTest(unittest.TestCase):
    """Drive the real driver against the fake serial firmware."""

    def test_identification_queries(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            self.assertIn("MX", mx.identify())
            self.assertEqual(mx.firmware_version().strip(";"), "0.0.2")
            self.assertIn("Matrix_Aktorblock", mx.hardware_version())
            self.assertEqual(mx.num_channels(), 24)
            self.assertTrue(mx.get_status())
            mx.close()

    def test_keepalive_pings_over_idle_and_stops_on_close(self):
        import time

        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", keepalive_interval=0.1)
            connect_ids = sum(
                1 for c, _ in fake.command_log if c.startswith("ID")
            )
            time.sleep(0.4)  # idle -> the keep-alive should ping a few times
            idle_ids = sum(
                1 for c, _ in fake.command_log if c.startswith("ID")
            )
            mx.close()
        # extra ID queries appeared over the idle gap...
        self.assertGreater(idle_ids, connect_ids)
        # ...and the thread is gone after close()
        self.assertFalse(
            any(t.name == "ibidi-keepalive" for t in threading.enumerate())
        )

    def test_keepalive_disabled_when_interval_zero(self):
        import time

        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", keepalive_interval=0)
            before = len(fake.command_log)
            time.sleep(0.3)
            self.assertEqual(len(fake.command_log), before)  # no pings
            self.assertFalse(
                any(t.name == "ibidi-keepalive" for t in threading.enumerate())
            )
            mx.close()

    def test_real_traffic_defers_keepalive_ping(self):
        # A command resets the idle clock, so the keep-alive does not ping on
        # top of active traffic (only over genuine gaps).
        import time

        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0, keepalive_interval=0.3)
            for _ in range(5):
                mx.select(3)  # steady real traffic, 50 ms apart
                time.sleep(0.05)
            ids = sum(1 for c, _ in fake.command_log if c.startswith("ID"))
            mx.close()
        # only the single connect-time ID; no keep-alive ping interleaved
        self.assertEqual(ids, 1)

    def test_is_a_hal_valve(self):
        self.assertTrue(issubclass(IbidiMultiplexer, ValveABC))

    def test_select_is_exclusive(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            mx.select(3)
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [3]
            )
            mx.select(20)
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [20]
            )
            mx.close()

    def test_select_multiple_channels_exclusively(self):
        # Unlike a rotary valve, several channels may be open at once — but
        # everything not listed must still be closed.
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            mx.select([1, 3])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [1, 3]
            )
            # switching to another set closes the previous one
            mx.select([2, 24])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [2, 24]
            )
            mx.close()

    def test_open_is_wire_zero_close_is_wire_one(self):
        # The device's bit is inverted with respect to flow: 0 opens.
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            mx.set_channel(5, True)
            self.assertIn(("SET:Valve:5:0", "OK;"), fake.command_log)
            self.assertTrue(fake.channels[4])
            self.assertTrue(mx.channel_states[4])
            mx.set_channel(5, False)
            self.assertIn(("SET:Valve:5:1", "OK;"), fake.command_log)
            self.assertFalse(fake.channels[4])
            self.assertFalse(mx.channel_states[4])
            mx.close()

    def test_batch_encodes_inverted_bits(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", batch_valves=True)
            mx.select([1, 3])
            batch = [
                c
                for c, _ in fake.command_log
                if c.startswith("SETBATCHVALVES")
            ]
            self.assertEqual(len(batch), 1)
            bits = batch[0].split("=", 1)[1].split(",")
            # open channels carry 0, everything else 1
            self.assertEqual(bits[0], "0")
            self.assertEqual(bits[2], "0")
            self.assertEqual(bits[1], "1")
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [1, 3]
            )
            mx.close()

    def test_sequential_is_the_default_and_closes_before_opening(self):
        # One valve at a time (the unit cannot actuate many at once), and
        # never a moment with an old and a new feed path open together.
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            self.assertFalse(mx.batch_valves)
            mx.select([2, 4])
            sets = [
                c for c, _ in fake.command_log if c.startswith("SET:Valve")
            ]
            self.assertFalse(
                [
                    c
                    for c, _ in fake.command_log
                    if c.startswith("SETBATCHVALVES")
                ]
            )
            self.assertEqual(len(sets), 24)  # every channel driven
            opens = [c for c in sets if c.endswith(":0")]
            self.assertEqual(opens, ["SET:Valve:2:0", "SET:Valve:4:0"])
            # all the closes come first
            self.assertLess(
                sets.index("SET:Valve:1:1"), sets.index("SET:Valve:2:0")
            )
            mx.close()

    def test_sequential_survives_the_units_current_limit(self):
        # The regression: a batch command asking many valves to switch at
        # once only actuates a few, so the route is silently wrong. One at a
        # time gets there.
        with emu.patch_ibidi_serial(max_simultaneous=3) as fake:
            mx = IbidiMultiplexer("7", batch_valves=True)
            mx.select([1, 6, 7, 8])
            self.assertNotEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [1, 6, 7, 8]
            )  # batch under-actuates
            mx.close()

        with emu.patch_ibidi_serial(max_simultaneous=3) as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            mx.select([1, 6, 7, 8])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [1, 6, 7, 8]
            )  # sequential gets the real route
            mx.close()

    def test_switch_delay_spaces_the_commands(self):
        import time as _time

        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7", switch_delay=0.002)
            started = _time.perf_counter()
            mx.select(1)
            elapsed = _time.perf_counter() - started
            # 24 channels -> 23 gaps; allow slack, just prove it waits.
            self.assertGreater(elapsed, 0.02)
            mx.close()

    def test_set_valve_accepts_channel_list(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7")
            mx.set_valve([4, 5])
            self.assertEqual(
                [i for i, s in enumerate(fake.channels, 1) if s], [4, 5]
            )
            mx.close()

    def test_select_rejects_bad_channel_sets(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            with self.assertRaises(ValueError):
                mx.select([])  # no channel at all
            with self.assertRaises(ValueError):
                mx.select([1, 25])  # out of range
            mx.close()

    def test_set_valve_matches_select(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7")
            mx.set_valve(7)
            self.assertTrue(fake.channels[6])
            self.assertEqual(sum(fake.channels), 1)
            mx.close()

    def test_open_all_close_all(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7", switch_delay=0)
            mx.open_all()
            self.assertTrue(all(fake.channels))
            self.assertTrue(all(mx.channel_states))
            mx.close_all()
            self.assertFalse(any(fake.channels))
            self.assertFalse(any(mx.channel_states))
            mx.close()

    def test_raw_set_all_marks_state_unknown(self):
        # SETALL/UNSETALL are firmware pass-throughs whose polarity we have
        # not confirmed on hardware, so the cache must not claim to know.
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            mx.set_all()
            self.assertTrue(all(s is None for s in mx.channel_states))
            mx.close()

    def test_set_single_channel(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7")
            mx.set_channel(5, True)
            self.assertTrue(fake.channels[4])
            mx.set_channel(5, False)
            self.assertFalse(fake.channels[4])
            mx.close()

    def test_channel_out_of_range_raises(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            with self.assertRaises(ValueError):
                mx.select(0)
            with self.assertRaises(ValueError):
                mx.select(25)
            mx.close()

    def test_batch_wrong_length_raises(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            with self.assertRaises(ValueError):
                mx.set_batch([1, 0, 1])
            mx.close()

    def test_clear_faults_and_status_registers(self):
        with emu.patch_ibidi_serial():
            mx = IbidiMultiplexer("7")
            self.assertIn("OK", mx.clear_faults())
            self.assertIn("DRV0", mx.read_status(0))
            mx.close()

    def test_abort_flag_skips_switch(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7")
            mx.abort_flag.set()
            mx.set_valve(4)
            # no channel opened while aborted
            self.assertFalse(any(fake.channels))
            mx.close()

    def test_connect_false_does_no_io(self):
        # No patch needed: constructor must not touch serial.
        mx = IbidiMultiplexer("7", connect=False)
        self.assertIsNone(mx._serial)
        with self.assertRaises(RuntimeError):
            mx.identify()

    def test_close_clears_buffers_then_closes_and_is_idempotent(self):
        # Graceful shutdown: purge the serial buffers before dropping the
        # handle (so stale bytes don't carry into the next session), but never
        # block draining output to a possibly-unresponsive device — a wedged
        # flush() would hang the whole shutdown.
        from unittest.mock import MagicMock, call

        mx = IbidiMultiplexer("7", connect=False)
        ser = MagicMock()
        ser.is_open = True
        mx._serial = ser
        mx.close()
        ser.reset_input_buffer.assert_called_once()
        ser.reset_output_buffer.assert_called_once()
        ser.close.assert_called_once()
        # pending output is discarded, not drained: flush() must NOT be called
        ser.flush.assert_not_called()
        # buffers are cleared before the port is closed, not after
        self.assertLess(
            ser.method_calls.index(call.reset_output_buffer()),
            ser.method_calls.index(call.close()),
        )
        self.assertIsNone(mx._serial)
        mx.close()  # second call is a no-op, must not raise

    def test_close_releases_the_fake_serial_port(self):
        with emu.patch_ibidi_serial() as fake:
            mx = IbidiMultiplexer("7")
            self.assertTrue(fake.is_open)
            mx.close()
            self.assertFalse(fake.is_open)


class IbidiLegacyArchitectureIntegrationTest(unittest.TestCase):
    """Build the real LegacyArchitecture from the IbidiEmulator setup."""

    def setUp(self):
        # valve_a / reservoir_a are class-level in LegacyArchitecture and
        # accumulate across instances; reset so a prior test's Hamilton valves
        # don't linger in _test_communication.
        import PycroFlow.hamilton_architecture as ha
        from PycroFlow.hamilton_components import ReservoirDict

        ha.LegacyArchitecture.valve_a = {}
        ha.LegacyArchitecture.reservoir_a = ReservoirDict()
        self._saved_abort = ham.communication.abort_wait_response_flag
        ham.communication.abort_wait_response_flag = threading.Event()

    def tearDown(self):
        ham.communication.abort_wait_response_flag = self._saved_abort

    def _build(self):
        from PycroFlow.configs import load_setup, assemble_hamilton_config
        import PycroFlow.hamilton_architecture as ha

        setup = load_setup("IbidiEmulator")
        settings = {
            "reservoir_names": {1: "imager1", 2: "imager2", 5: "buffer"},
            "special_names": {"flushbuffer_a": 5},
        }
        hamilton, tubing = assemble_hamilton_config(setup, settings)
        with emu.patch_serial(), emu.patch_ibidi_serial() as fake:
            ha.connect(
                hamilton["interface"]["COM"], hamilton["interface"]["baud"]
            )
            la = ha.LegacyArchitecture(hamilton, tubing)
        return la, fake

    def test_builds_with_multiplexer(self):
        la, fake = self._build()
        self.assertIsNotNone(la.multiplexer)
        self.assertIsInstance(la.multiplexer, IbidiMultiplexer)
        self.assertIn("ibidi", la.valve_a)

    def test_close_releases_the_multiplexer_port(self):
        # Deterministic shutdown: LegacyArchitecture.close() must release the
        # ibidi serial port (rather than leaving it to __del__ / GC), and be
        # safe to call more than once.
        la, fake = self._build()
        self.assertTrue(fake.is_open)
        la.close()
        self.assertFalse(fake.is_open)
        la.close()  # idempotent — must not raise

    def test_close_still_releases_port_when_spill_sensor_fails(self):
        # A failure stopping the spill sensor must not leave the ibidi port
        # held: mux.close() still has to run, and the single-attempt _closed
        # guard must not strand it.
        from unittest.mock import MagicMock

        la, fake = self._build()
        la.spill_sensor = MagicMock()
        la.spill_sensor.stop_monitoring.side_effect = RuntimeError("boom")
        la.close()
        self.assertFalse(fake.is_open)

    def test_set_valves_routes_multi_channel_reservoir(self):
        # A reservoir wired through several channels (valve_pos {ibidi: [..]})
        # opens exactly those, all the way from _set_valves.
        from PycroFlow.configs import assemble_hamilton_config, load_setup
        import PycroFlow.hamilton_architecture as ha

        setup = load_setup("IbidiEmulator")
        for entry in setup["fluid"]["reservoirs"]:
            if entry["id"] == 3:
                entry["valve_pos"] = {"ibidi": [1, 3], 1: "in"}
        hamilton, tubing = assemble_hamilton_config(
            setup,
            {
                "reservoir_names": {1: "imager1", 3: "shared", 5: "buffer"},
                "special_names": {"flushbuffer_a": 5},
            },
        )
        with emu.patch_serial(), emu.patch_ibidi_serial() as fake:
            ha.connect(
                hamilton["interface"]["COM"], hamilton["interface"]["baud"]
            )
            la = ha.LegacyArchitecture(hamilton, tubing)
        la._set_valves(3)
        self.assertEqual(
            [i for i, s in enumerate(fake.channels, 1) if s], [1, 3]
        )
        # a single-channel reservoir still closes the extra channel
        la._set_valves(1)
        self.assertEqual([i for i, s in enumerate(fake.channels, 1) if s], [1])

    def test_design_edit_after_connect_is_applied(self):
        # The reported failure: a reservoir added to the design after the
        # hardware was connected made the run die on its first step with
        # KeyError in _set_valves. Re-syncing must make it routable.
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("IbidiEmulator")
        first = {
            "settings": {
                "reservoir_names": {2: "Buffer", 4: "Imager 2"},
                "special_names": {},
            }
        }
        fs = svc.connect_fluid(first)
        self.assertEqual(sorted(fs.reservoir_paths), [2, 4])

        # ... the user then adds "Imager 1" on reservoir 3 and re-translates
        edited = {
            "settings": {
                "reservoir_names": {2: "Buffer", 4: "Imager 2", 3: "Imager 1"},
                "special_names": {},
            }
        }
        self.assertTrue(svc.sync_fluid_reservoirs(edited))
        self.assertEqual(sorted(fs.reservoir_paths), [2, 3, 4])
        fs._set_valves(3)
        self.assertEqual(
            [i for i, s in enumerate(fs.multiplexer.channel_states, 1) if s],
            [3],
        )

    def test_sync_drops_removed_reservoirs(self):
        # Rebuilt, not added to: a reservoir taken out of the design must
        # stop being routable rather than linger from the previous connect.
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("IbidiEmulator")
        fs = svc.connect_fluid(
            {
                "settings": {
                    "reservoir_names": {2: "Buffer", 4: "Imager 2"},
                    "special_names": {},
                }
            }
        )
        svc.sync_fluid_reservoirs(
            {
                "settings": {
                    "reservoir_names": {2: "Buffer"},
                    "special_names": {},
                }
            }
        )
        self.assertEqual(sorted(fs.reservoir_paths), [2])

    def test_manual_set_valves_reaches_undesigned_reservoirs(self):
        # Manual hardware testing must reach every reservoir the SETUP wires,
        # not just the ones the loaded design happens to name.
        from PycroFlow.services import SystemService

        svc = SystemService()
        svc.load_setup("IbidiEmulator")
        fs = svc.connect_fluid(
            {
                "settings": {
                    "reservoir_names": {1: "imager1"},
                    "special_names": {"flushbuffer_a": 5},
                }
            }
        )
        self.assertEqual(sorted(fs.reservoir_a.keys()), [1, 5])

        svc.set_valves(8)  # wired in the setup, absent from the design
        self.assertEqual(
            [i for i, s in enumerate(fs.multiplexer.channel_states, 1) if s],
            [8],
        )
        with self.assertRaises(KeyError):
            svc.set_valves(99)  # wired nowhere

    def test_set_valves_routes_through_multiplexer(self):
        la, fake = self._build()
        # The fake serial persists on the constructed multiplexer after the
        # patch context exits, so _set_valves drives it directly. _set_valves
        # opens the channel matching the reservoir id and sets the pump input
        # position; verify via the fake's channel state.
        la._set_valves(2)
        self.assertEqual([i for i, s in enumerate(fake.channels, 1) if s], [2])
        la._set_valves(1)
        self.assertEqual([i for i, s in enumerate(fake.channels, 1) if s], [1])


if __name__ == "__main__":
    unittest.main()


class _FlakySerial:
    """Serial stub whose write times out the first ``fail_times`` calls."""

    def __init__(self, fail_times):
        self._fail_times = fail_times
        self._writes = 0
        self._last = b""

    def write(self, data):
        self._writes += 1
        if self._writes <= self._fail_times:
            from serial import SerialTimeoutException

            raise SerialTimeoutException("Write timeout")
        self._last = data

    def read_until(self, expected=b";", size=None):
        return b"OK;"

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


class IbidiRetryTest(unittest.TestCase):
    """Serial-write safety margin: retry transient timeouts, raise on
    persistent ones (so orchestration can abort loudly)."""

    def _mx(self, fail_times):
        from PycroFlow.ibidi_multiplexer import IbidiMultiplexer

        mx = IbidiMultiplexer(
            "7", connect=False, command_retries=3, retry_delay=0
        )
        mx._serial = _FlakySerial(fail_times)
        mx._port = None  # no reopen; retries hit the same (recovering) stub
        return mx

    def test_transient_timeout_is_retried(self):
        mx = self._mx(fail_times=2)  # 3rd write succeeds, within 3 retries
        self.assertTrue(mx._ok(mx._command("SET:Valve:3:0")))

    def test_persistent_timeout_raises_after_retries(self):
        from serial import SerialException, SerialTimeoutException

        mx = self._mx(fail_times=99)  # never recovers
        with self.assertRaises(SerialException) as ctx:
            mx._command("SET:Valve:3:0")
        # 1 initial + 3 retries = 4 write attempts.
        self.assertEqual(mx._serial._writes, 4)
        # The original timeout is preserved as the cause (not swallowed).
        self.assertIsInstance(ctx.exception.__cause__, SerialTimeoutException)

    def test_open_serial_sets_write_timeout(self):
        """The real port is opened with a bounded write_timeout, so a write to
        a dead/suspended link raises (feeding the retry/abort path) instead of
        blocking the calling thread forever."""
        import unittest.mock as mock

        import PycroFlow.ibidi_multiplexer as mod

        captured = {}

        class _RecSerial:
            def __init__(self, *a, **k):
                captured.update(k)

            def close(self):
                pass

        with mock.patch.object(mod, "Serial", _RecSerial):
            # Default: a non-None write_timeout is always set.
            mx = IbidiMultiplexer("7", connect=False)
            mx._port = "COM7"
            mx._open_serial()
            self.assertIsNotNone(captured.get("write_timeout"))
            # Explicit override is honored.
            mx2 = IbidiMultiplexer("7", connect=False, write_timeout=5.0)
            mx2._port = "COM7"
            mx2._open_serial()
        self.assertEqual(captured.get("write_timeout"), 5.0)


class _ReconnSerial:
    """Serial stub whose writes time out until it is marked ``healthy``.

    A fresh instance is 'dropped' (writes raise); the reopen during reconnect
    hands back a healthy one. ``read_until`` returns ``reply`` (``MX;`` so the
    reconnect ID probe succeeds)."""

    def __init__(self, *a, **k):
        self.healthy = False
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if not self.healthy:
            from serial import SerialTimeoutException

            raise SerialTimeoutException("Write timeout")

    def read_until(self, expected=b";", size=None):
        return b"MX;"

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


class _Port:
    def __init__(self, device, vid, pid, serial_number):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number


class IbidiReconnectTest(unittest.TestCase):
    """Auto-reconnect: a dropped link recovers transparently (same port or the
    same USB device on a new port), and still aborts after the window."""

    def _mx(self, **kw):
        from PycroFlow.ibidi_multiplexer import IbidiMultiplexer

        kw.setdefault("command_retries", 0)
        kw.setdefault("retry_delay", 0)
        mx = IbidiMultiplexer("7", connect=False, **kw)
        mx._port = "COM7"
        mx._serial = _ReconnSerial()  # 'dropped' link (writes time out)
        mx._connected = True  # a previously-connected device
        return mx

    def test_command_recovers_via_reconnect(self):
        import unittest.mock as mock
        import PycroFlow.ibidi_multiplexer as mod

        mx = self._mx(reconnect_timeout=5)
        healthy = _ReconnSerial()
        healthy.healthy = True
        # The reopen during recovery hands back a working device.
        with mock.patch.object(mod, "Serial", return_value=healthy):
            reply = mx._command("ID")
        self.assertIn("MX", reply)  # command completed, no raise
        self.assertIs(mx._serial, healthy)  # relinked to the live handle

    def test_dropped_link_raises_after_reconnect_window(self):
        import unittest.mock as mock
        from serial import SerialException
        import PycroFlow.ibidi_multiplexer as mod

        mx = self._mx(reconnect_timeout=0.3)
        # Reopen always fails -> link never recovers -> window expires -> raise
        # (so the orchestrator still aborts a genuinely dead device).
        with mock.patch.object(
            mod, "Serial", side_effect=SerialException("x")
        ):
            with self.assertRaises(SerialException):
                mx._command("SET:Valve:3:0")

    def test_reconnect_disabled_raises_immediately(self):
        from serial import SerialException, SerialTimeoutException

        mx = self._mx(reconnect_timeout=0)  # auto-reconnect off
        with self.assertRaises(SerialException) as ctx:
            mx._command("SET:Valve:3:0")
        self.assertIsInstance(ctx.exception.__cause__, SerialTimeoutException)
        self.assertEqual(mx._serial.writes, 1)  # no recovery attempted

    def test_relink_candidates_same_port_first_then_matching_device_only(self):
        import unittest.mock as mock
        from serial.tools import list_ports

        mx = self._mx()
        mx._usb_id = (0x1234, 0x5678, "SER123")
        ports = [
            _Port("COM9", 0x1234, 0x5678, "SER123"),  # same device, new port
            _Port("COM3", 0x1111, 0x2222, "OTHER"),  # a DIFFERENT instrument
        ]
        with mock.patch.object(list_ports, "comports", return_value=ports):
            cands = mx._relink_candidates()
        # Same port first, then the matched device — NEVER the foreign COM3.
        self.assertEqual(cands, ["COM7", "COM9"])

    def test_no_recovery_before_connect_handshake(self):
        # During the initial connect()/identify(), _connected is False, so a
        # wedged device raises fast instead of hanging the whole window.
        import unittest.mock as mock
        from serial import SerialException

        mx = self._mx(reconnect_timeout=120)
        mx._connected = False  # as during the connect handshake
        with mock.patch.object(
            mx, "_recover_link", side_effect=AssertionError("must not recover")
        ):
            with self.assertRaises(SerialException):
                mx._command("ID")

    def test_write_read_raises_cleanly_after_close(self):
        # close() sets self._serial = None; a concurrent _write_read must raise
        # SerialException, not AttributeError on None.
        from serial import SerialException

        mx = self._mx()
        mx._serial = None
        with self.assertRaises(SerialException):
            mx._write_read("ID", b"ID;")

    def test_relink_does_not_resurrect_handle_after_close(self):
        # If close() nulled the handle + disarmed, _try_relink bails rather than
        # re-opening the port after close.
        import unittest.mock as mock
        import PycroFlow.ibidi_multiplexer as mod

        mx = self._mx()
        mx._serial = None
        mx._connected = False  # close() ran
        with mock.patch.object(mod, "Serial") as serial_cls:
            self.assertFalse(mx._try_relink())
        serial_cls.assert_not_called()  # never re-opened the port

    def test_record_usb_identity_matches_connected_port(self):
        import unittest.mock as mock
        from serial.tools import list_ports

        mx = self._mx()
        ports = [
            _Port("COM3", 0x1111, 0x2222, "OTHER"),
            _Port("COM7", 0x1234, 0x5678, "SER123"),
        ]
        with mock.patch.object(list_ports, "comports", return_value=ports):
            mx._record_usb_identity()
        self.assertEqual(mx._usb_id, (0x1234, 0x5678, "SER123"))
