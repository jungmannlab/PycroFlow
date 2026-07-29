"""Tests for the hardware emulators (and demonstrations of their use).

These cover the emulators themselves and, importantly, drive the *real* drivers
against them so the wire-protocol encode/decode path gets genuine coverage.
"""

import threading
import unittest

import PycroFlow.pyHamilton as ham
from PycroFlow.hal import (
    Pump as PumpABC,
    Valve as ValveABC,
    SpillSensor as SpillABC,
)
from PycroFlow.tests import emulators as emu


class HamiltonSerialEmulatorTest(unittest.TestCase):
    """Drive the real Pump / Valve / SerialBus against the serial emulator."""

    def setUp(self):
        self.flag = threading.Event()
        # wait_until_done consults this module-level flag; default is None.
        self._saved_abort = ham.communication.abort_wait_response_flag
        ham.communication.abort_wait_response_flag = threading.Event()

    def tearDown(self):
        ham.communication.abort_wait_response_flag = self._saved_abort

    def _make_pump(self, fake):
        from PycroFlow.hamilton_components import Pump

        pump = Pump(
            "2",
            "500u",
            instrument_type="4",
            valve_type="Y",
            output_pos="out",
            input_pos="in",
            waste_pos=1,
            pause_flag=self.flag,
            abort_flag=self.flag,
        )
        return pump

    def test_pickup_dispense_round_trip(self):
        with emu.patch_serial() as fake:
            ham.connect("18", 9600)
            pump = self._make_pump(fake)

            pump.pickup(250, waitForPump=True)
            self.assertAlmostEqual(pump.get_current_volume(), 250.0, delta=1.0)

            pump.dispense(100, waitForPump=True)
            self.assertAlmostEqual(pump.get_current_volume(), 150.0, delta=1.0)

            # The emulated device accumulated steps from the wire.
            self.assertGreater(fake.device("3").syringe_steps, 0)

    def test_valve_moves_are_tracked(self):
        from PycroFlow.hamilton_components import Valve

        with emu.patch_serial() as fake:
            ham.connect("18", 9600)
            valve = Valve("1", "MVP", "8-5")
            valve.pause_flag = self.flag
            valve.abort_flag = self.flag

            valve.set_valve(3)
            # ascii address of switch address '1' is '2'.
            self.assertEqual(fake.device("2").valve_pos, 3)
            valve.set_valve(5)
            self.assertEqual(fake.device("2").valve_pos, 5)

            # Command log captured the addressed frames.
            addrs = {a for a, _ in fake.command_log}
            self.assertIn("2", addrs)

    def test_make_fake_bus_helper(self):
        bus = emu.make_fake_bus()
        ham.communication.set_bus(bus)
        try:
            resp = bus.send_command("3", "?")  # absolute syringe position
            self.assertIn("`", resp)
            self.assertTrue(resp.endswith("\r\n"))
        finally:
            ham.communication.set_bus(ham.communication.SerialBus())

    def test_unaddressed_frame_does_not_hang(self):
        fake = emu.FakeHamiltonSerial()
        fake.open()
        fake.write(b"garbage\r\n")
        line = fake.readline()
        self.assertTrue(line.endswith(b"\r\n"))


class HalDeviceEmulatorTest(unittest.TestCase):
    def test_emulators_satisfy_abcs(self):
        self.assertIsInstance(emu.EmulatedPump(), PumpABC)
        self.assertIsInstance(emu.EmulatedValve(), ValveABC)
        self.assertIsInstance(emu.EmulatedSpillSensor(), SpillABC)

    def test_pump_volume_tracking(self):
        pump = emu.EmulatedPump(syringe_volume=500)
        pump.set_valve("in")
        pump.pickup(300, waitForPump=True)
        self.assertEqual(pump.get_current_volume(), 300)
        pump.set_valve("out")
        pump.dispense(100, waitForPump=True)
        self.assertEqual(pump.get_current_volume(), 200)
        # Clamped at the syringe capacity.
        pump.pickup(10000, waitForPump=True)
        self.assertEqual(pump.get_current_volume(), 500)
        # Command log records the sequence.
        methods = [m for m, _ in pump.commands]
        self.assertEqual(
            methods[:4], ["set_valve", "pickup", "set_valve", "dispense"]
        )

    def test_pump_async_then_wait(self):
        pump = emu.EmulatedPump(syringe_volume=500)
        pump.pickup(120)  # waitForPump=False -> not applied yet
        self.assertEqual(pump.get_current_volume(), 0)
        self.assertTrue(pump.moving)
        pump.wait_until_done()
        self.assertEqual(pump.get_current_volume(), 120)
        self.assertFalse(pump.moving)

    def test_valve_deferred_move(self):
        valve = emu.EmulatedValve()
        valve.set_valve(4, move_now=False)
        self.assertIsNone(valve.position)
        self.assertIn("moving", valve.get_status())
        valve.wait_until_done()
        self.assertEqual(valve.position, 4)

    def test_spill_sensor_poll(self):
        sensor = emu.EmulatedSpillSensor()
        self.assertIsNone(sensor.poll_sensor())  # not connected yet
        self.assertTrue(sensor.connect())
        self.assertFalse(sensor.poll_sensor())
        sensor.set_wet(True)
        self.assertTrue(sensor.poll_sensor())
        sensor.disconnect()

    def test_spill_sensor_monitor_callback(self):
        sensor = emu.EmulatedSpillSensor(poll_interval=0.005)
        sensor.connect()
        fired = threading.Event()
        msgs = []

        def on_wet(msg):
            msgs.append(msg)
            fired.set()

        sensor.monitor_sensor(fn_on_wet=on_wet)
        sensor.set_wet(True)
        self.assertTrue(fired.wait(timeout=2), "wet callback never fired")
        self.assertEqual(len(msgs), 1)
        sensor.stop_monitoring()


class ArduinoSerialEmulatorTest(unittest.TestCase):
    def test_poll_dry_then_wet(self):
        with emu.connect_interface() as iface:
            self.assertTrue(iface.is_connected)
            self.assertFalse(iface.poll_sensor())
            iface.serial_conn.wet = True
            self.assertTrue(iface.poll_sensor())

    def test_monitor_fires_on_wet(self):
        fired = threading.Event()
        with emu.connect_interface(wet=True) as iface:
            iface.monitor_sensor(fn_on_wet=lambda msg: fired.set())
            self.assertTrue(fired.wait(timeout=2))
            iface.stop_monitoring()

    def test_handshake_recorded(self):
        with emu.connect_interface() as iface:
            self.assertIn("H", iface.serial_conn.written)


class SubsystemEmulatorTest(unittest.TestCase):
    def test_fluid_system_injects_through_pump(self):
        import PycroFlow.orchestration as por
        from PycroFlow.orchestration import ThreadExchange

        protocol = {
            "protocol_entries": [
                {"$type": "inject", "reservoir_id": 2, "volume": 300},
                {
                    "$type": "inject",
                    "reservoir_id": 5,
                    "volume": 150,
                    "velocity": 600,
                },
            ]
        }
        fluid = emu.EmulatedFluidSystem()
        tx = ThreadExchange.create()
        handler = por.FluidHandler(fluid, protocol, tx)
        handler.execute_protocol_entry(0)
        handler.execute_protocol_entry(1)

        self.assertEqual(fluid.injections, [(2, 300), (5, 150)])
        # After inject the syringe ends empty (picked up then dispensed).
        self.assertEqual(fluid.pump.get_current_volume(), 0)
        # The last reservoir valve selection stuck.
        self.assertEqual(fluid.valve.position, 5)

    def test_orchestration_end_to_end_with_emulated_systems(self):
        import PycroFlow.orchestration as por

        protocol = {
            "fluid": {
                "protocol_entries": [
                    {"$type": "inject", "reservoir_id": 0, "volume": 100},
                    {"$type": "signal", "value": "fluid round 1 done"},
                    {
                        "$type": "wait for signal",
                        "target": "img",
                        "value": "imaging round 1 done",
                    },
                ]
            },
            "img": {
                "protocol_entries": [
                    {
                        "$type": "wait for signal",
                        "target": "fluid",
                        "value": "fluid round 1 done",
                    },
                    {
                        "$type": "acquire",
                        "frames": 1000,
                        "t_exp": 100,
                        "message": "r1",
                    },
                    {"$type": "signal", "value": "imaging round 1 done"},
                ]
            },
        }
        fluid = emu.EmulatedFluidSystem()
        imaging = emu.EmulatedImagingSystem()
        po = por.ProtocolOrchestrator(
            protocol, fluid_system=fluid, imaging_system=imaging
        )
        po.start_orchestration()
        po.start_protocol()

        import time

        deadline = time.time() + 5
        while time.time() < deadline and not po.poll_protocol_finished():
            time.sleep(0.05)
        po.end_orchestration()

        self.assertIn("fluid round 1 done", po.threadexchange["fluid"])
        self.assertIn("imaging round 1 done", po.threadexchange["img"])
        self.assertEqual(len(imaging.acquisitions), 1)
        self.assertEqual(fluid.injections, [(0, 100)])


if __name__ == "__main__":
    unittest.main()
