"""Tests for :class:`PycroFlow.pyHamilton.communication.SerialBus` lifecycle.

Focus on the reconnect path: re-initialising the bus must release the port it
already holds, so a reconnect (e.g. re-applying a changed design) never fails
with the port "already occupied".
"""

import unittest
from unittest import mock

import PycroFlow.tests  # noqa: F401  (installs hardware mocks)
from PycroFlow.pyHamilton.communication import SerialBus
from PycroFlow.tests.emulators.hamilton_serial import FakeHamiltonSerial


class TestSerialBusReconnect(unittest.TestCase):

    def _patch_serial(self):
        """Patch serial.Serial to hand out a fresh fake each construction."""
        created = []

        def _factory(*args, **kwargs):
            fake = FakeHamiltonSerial()
            created.append(fake)
            return fake

        patcher = mock.patch(
            "PycroFlow.pyHamilton.communication.serial.Serial",
            side_effect=_factory,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return created

    def test_reinitialize_closes_the_previous_port(self):
        created = self._patch_serial()
        bus = SerialBus()
        bus.initialize("3", 9600)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].isOpen())

        # Re-initialise WITHOUT an explicit disconnect (the failure mode): the
        # old handle must be closed so the OS frees the port for the new open.
        bus.initialize("3", 9600)
        self.assertEqual(len(created), 2)
        self.assertFalse(created[0].isOpen())  # old port released
        self.assertTrue(created[1].isOpen())  # new port open

    def test_disconnect_drops_the_handle(self):
        self._patch_serial()
        bus = SerialBus()
        bus.initialize("3", 9600)
        bus.disconnect()
        self.assertIsNone(bus.ser)
        # A second disconnect is a harmless no-op.
        bus.disconnect()
        self.assertIsNone(bus.ser)


if __name__ == "__main__":
    unittest.main()
