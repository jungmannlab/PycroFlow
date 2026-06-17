"""Serial-level emulator for the Arduino spill-sensor firmware.

Drop-in for ``serial.Serial`` so the real
:class:`PycroFlow.spill_sensor_arduino.ArduinoSensorInterface` runs against it.
The firmware protocol (see that module) is a single-byte command / line reply:

====  ==================  ===================
cmd   meaning             reply
====  ==================  ===================
H     handshake           ``HANDSHAKE_OK``
P     poll sensor         ``WET`` / ``DRY``
B     start broadcast     ``START_BROADCAST_OK``
S     stop broadcast      ``STOP_BROADCAST_OK``
R     reset               (no reply)
====  ==================  ===================

Drive the wet/dry reading with :attr:`wet`.

Convenience: :func:`connect_interface` builds an ``ArduinoSensorInterface``
already attached to a fake (patching out the 2 s init sleep), so a test can poll
or monitor immediately.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest import mock


class FakeArduinoSerial:
    """Emulated Arduino serial port for the spill sensor."""

    def __init__(self, *args, wet=False, **kwargs):
        self.wet = wet
        self._open = True
        self._rx = bytearray()
        self.written = []  # every command byte written, for assertions

    # -- serial.Serial surface ------------------------------------------------
    @property
    def is_open(self):
        return self._open

    def close(self):
        self._open = False

    def flush(self):
        pass

    def flushInput(self):
        self._rx.clear()

    def flushOutput(self):
        pass

    @property
    def in_waiting(self):
        return len(self._rx)

    def write(self, data):
        text = data.decode() if isinstance(data, (bytes, bytearray)) else data
        for ch in text:
            self.written.append(ch)
            self._rx.extend((self._reply_for(ch)).encode())
        return len(data)

    def readline(self):
        idx = self._rx.find(b'\n')
        if idx == -1:
            line, self._rx = bytes(self._rx), bytearray()
            return line
        line = bytes(self._rx[:idx + 1])
        del self._rx[:idx + 1]
        return line

    # -- protocol -------------------------------------------------------------
    def _reply_for(self, ch):
        if ch == 'H':
            return 'HANDSHAKE_OK\n'
        if ch == 'P':
            return ('WET' if self.wet else 'DRY') + '\n'
        if ch == 'B':
            return 'START_BROADCAST_OK\n'
        if ch == 'S':
            return 'STOP_BROADCAST_OK\n'
        if ch == 'R':
            return ''
        return ''


@contextmanager
def connect_interface(port='COM-EMU', wet=False):
    """Yield a connected ``ArduinoSensorInterface`` backed by a fake serial.

    Patches ``serial.Serial`` in the spill-sensor module and ``time.sleep`` so
    the real ``connect`` handshake completes instantly. The yielded interface is
    already ``is_connected``; its ``.serial_conn`` is the
    :class:`FakeArduinoSerial`, whose ``.wet`` attribute drives the reading.
    """
    from PycroFlow import spill_sensor_arduino as ssa

    fake = FakeArduinoSerial(wet=wet)
    with mock.patch.object(ssa.serial, 'Serial', return_value=fake), \
            mock.patch.object(ssa.time, 'sleep', lambda *a, **k: None):
        iface = ssa.ArduinoSensorInterface(port=port)
        ok = iface.connect()
        if not ok:
            raise RuntimeError('emulated Arduino handshake failed')
        try:
            yield iface
        finally:
            iface.disconnect()
