"""Tests for ArduinoSensorInterface, driven by the FakeArduinoSerial emulator.

Covers connect/handshake, polling (wet/dry/not-connected/garbage/error),
background monitoring, broadcast control, port auto-detection, and disconnect —
none of which need a real Arduino.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from PycroFlow import spill_sensor_arduino as ssa
from PycroFlow.tests.emulators import connect_interface, FakeArduinoSerial


class ConnectTest(unittest.TestCase):
    def test_connect_handshake_success(self):
        with connect_interface() as iface:
            self.assertTrue(iface.is_connected)
            self.assertIn('H', iface.serial_conn.written)

    def test_connect_handshake_failure_returns_false(self):
        # A serial whose handshake reply is wrong -> connect() returns False.
        bad = FakeArduinoSerial()
        bad._reply_for = lambda ch: 'NOPE\n'
        with patch.object(ssa.serial, 'Serial', return_value=bad), \
                patch.object(ssa.time, 'sleep', lambda *a, **k: None):
            iface = ssa.ArduinoSensorInterface(port='COM-EMU')
            self.assertFalse(iface.connect())
            self.assertFalse(iface.is_connected)


class PollTest(unittest.TestCase):
    def test_poll_dry_then_wet(self):
        with connect_interface() as iface:
            self.assertFalse(iface.poll_sensor())
            iface.serial_conn.wet = True
            self.assertTrue(iface.poll_sensor())

    def test_poll_when_not_connected_returns_none(self):
        iface = ssa.ArduinoSensorInterface(port='COM-EMU')
        self.assertIsNone(iface.poll_sensor())

    def test_poll_unexpected_response_returns_none(self):
        with connect_interface() as iface:
            iface.serial_conn._reply_for = lambda ch: 'GIBBERISH\n'
            self.assertIsNone(iface.poll_sensor())

    def test_poll_serial_error_returns_none(self):
        with connect_interface() as iface:
            iface.serial_conn.write = MagicMock(side_effect=OSError('boom'))
            self.assertIsNone(iface.poll_sensor())


class MonitorTest(unittest.TestCase):
    def test_monitor_fires_callback_on_wet(self):
        fired = threading.Event()
        with connect_interface(wet=True) as iface:
            iface.monitor_sensor(fn_on_wet=lambda msg: fired.set())
            self.assertTrue(fired.wait(timeout=2))
            iface.stop_monitoring()

    def test_stop_monitoring_safe_when_never_started(self):
        iface = ssa.ArduinoSensorInterface(port='COM-EMU')
        # No monitor thread exists yet; stop must not raise.
        iface.stop_monitoring()


class BroadcastTest(unittest.TestCase):
    def test_start_and_stop_broadcast(self):
        with connect_interface() as iface:
            self.assertTrue(iface.start_broadcast())
            self.assertTrue(iface.stop_broadcast())

    def test_broadcast_when_not_connected_returns_none(self):
        iface = ssa.ArduinoSensorInterface(port='COM-EMU')
        self.assertIsNone(iface.start_broadcast())
        self.assertIsNone(iface.stop_broadcast())


class PortDiscoveryTest(unittest.TestCase):
    def test_find_arduino_port_matches_description(self):
        fake_port = MagicMock(device='COM7', description='Arduino Uno')
        other = MagicMock(device='COM1', description='Bluetooth')
        with patch.object(ssa.serial.tools.list_ports, 'comports',
                          return_value=[other, fake_port]):
            iface = ssa.ArduinoSensorInterface()
            self.assertEqual(iface.find_arduino_port(), 'COM7')

    def test_find_arduino_port_none_when_absent(self):
        other = MagicMock(device='COM1', description='Bluetooth')
        with patch.object(ssa.serial.tools.list_ports, 'comports',
                          return_value=[other]):
            iface = ssa.ArduinoSensorInterface()
            self.assertIsNone(iface.find_arduino_port())

    def test_connect_without_port_uses_discovery_failure(self):
        with patch.object(ssa.serial.tools.list_ports, 'comports',
                          return_value=[]):
            iface = ssa.ArduinoSensorInterface()  # no port given
            self.assertFalse(iface.connect())


class DisconnectTest(unittest.TestCase):
    def test_disconnect_closes_and_clears(self):
        with connect_interface() as iface:
            conn = iface.serial_conn
            iface.disconnect()
            self.assertFalse(iface.is_connected)
            self.assertIsNone(iface.serial_conn)
            self.assertFalse(conn.is_open)
            # Second disconnect is a no-op.
            iface.disconnect()


if __name__ == '__main__':
    unittest.main()
