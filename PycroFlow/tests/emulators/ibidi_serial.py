"""Serial-level emulator for the ibidi MultiFlOW 24-channel actuator unit.

Drop-in for ``serial.Serial`` so the *real*
:class:`PycroFlow.ibidi_multiplexer.IbidiMultiplexer` runs end to end against
the emulated firmware (covering the command encode / response decode path that
a ``MagicMock`` cannot). The firmware protocol (see ``MultiFlOW_Commands_FW``
and ``PycroFlow.ibidi_multiplexer``) is ``;``-terminated request / reply:

========================================  ====================================
command                                   reply
========================================  ====================================
``ID;`` / ``*IDN?;``                      ``MX;``
``FWV;``                                  ``0.0.2;``
``HWV;``                                  ``Matrix_Aktorblock_P1.1;``
``CH;``                                   ``24;``
``SETALL;`` / ``UNSETALL;``               ``OK;``
``SET:Valve:<v>:<s>;``                    ``OK;`` / ``counterr;`` / ``Unknown``
``SETBATCHVALVES=<24 csv bits>;``         ``OK;`` / ``biterr;`` / ``Counter;``
``CLRFLT;``                               ``OK;``
``RDSTAT=<idx>;``                         ``Status DRV<idx>: 0x00;``
``RDALLSTAT;``                            ``...OK;``
========================================  ====================================

The per-channel state is exposed via :attr:`channels` for assertions.

Typical use in a test::

    from PycroFlow.tests.emulators import ibidi_serial as ibs

    with ibs.patch_ibidi_serial() as fake:
        mx = IbidiMultiplexer('COM7')
        mx.select(3)
        assert fake.channels[2] is True
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from unittest import mock

FWV = "0.0.2"
HWV = "Matrix_Aktorblock_P1.1"
DEVICE_ID = "MX"

_RE_SET_VALVE = re.compile(r"^SET:Valve:(\d+):([01])$")
_RE_BATCH = re.compile(r"^SETBATCHVALVES=(.*)$")
_RE_RDSTAT = re.compile(r"^RDSTAT=(\d+)$")


class FakeIbidiSerial:
    """Emulated ibidi MultiFlOW serial port, 24 bi-stable channels."""

    def __init__(self, *args, channels=24, **kwargs):
        self.num_channels = channels
        self.channels = [False] * channels
        self._open = True
        self._rx = bytearray()
        self._lock = threading.Lock()
        # Every (command, reply) pair handled, for assertions.
        self.command_log = []
        # serial.Serial attributes the driver may set / read.
        self.port = args[0] if args else kwargs.get("port")
        self.baudrate = kwargs.get("baudrate")
        self.timeout = kwargs.get("timeout")

    # -- serial.Serial surface ------------------------------------------------
    @property
    def is_open(self):
        return self._open

    def isOpen(self):
        return self._open

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def flush(self):
        pass

    def reset_input_buffer(self):
        with self._lock:
            self._rx.clear()

    def reset_output_buffer(self):
        pass

    @property
    def in_waiting(self):
        return len(self._rx)

    def write(self, data):
        text = data.decode() if isinstance(data, (bytes, bytearray)) else data
        for frame in self._split_frames(text):
            reply = self._handle(frame)
            with self._lock:
                self._rx.extend(reply.encode())
        return len(data)

    def read_until(self, expected=b";", size=None):
        with self._lock:
            idx = self._rx.find(expected)
            if idx == -1:
                out, self._rx = bytes(self._rx), bytearray()
            else:
                end = idx + len(expected)
                out = bytes(self._rx[:end])
                del self._rx[:end]
        return out

    def readline(self):
        return self.read_until(b"\n")

    def read(self, size=1):
        with self._lock:
            chunk = bytes(self._rx[:size])
            del self._rx[:size]
        return chunk

    # -- firmware protocol ----------------------------------------------------
    @staticmethod
    def _split_frames(text):
        # Commands are ';'-terminated; tolerate trailing CR/LF and batching.
        return [f.strip() for f in re.split(r";", text) if f.strip()]

    def _handle(self, cmd):
        reply = self._reply_for(cmd)
        self.command_log.append((cmd, reply))
        return reply

    def _reply_for(self, cmd):
        if cmd in ("ID", "*IDN?"):
            return DEVICE_ID + ";"
        if cmd == "FWV":
            return FWV + ";"
        if cmd == "HWV":
            return HWV + ";"
        if cmd == "CH":
            return "{};".format(self.num_channels)
        if cmd == "SETALL":
            self.channels = [True] * self.num_channels
            return "OK;"
        if cmd == "UNSETALL":
            self.channels = [False] * self.num_channels
            return "OK;"
        if cmd == "CLRFLT":
            return "OK;"
        if cmd == "RDALLSTAT":
            return "Status DRV0: 0x00; Status DRV1: 0x00; OK;"

        m = _RE_RDSTAT.match(cmd)
        if m:
            return "Status DRV{}: 0x00;".format(int(m.group(1)))

        m = _RE_SET_VALVE.match(cmd)
        if m:
            valve, state = int(m.group(1)), int(m.group(2))
            if not (1 <= valve <= self.num_channels):
                return "counterr;"
            self.channels[valve - 1] = bool(state)
            return "OK;"

        m = _RE_BATCH.match(cmd)
        if m:
            parts = m.group(1).split(",")
            if any(p.strip() not in ("0", "1") for p in parts):
                return "biterr;"
            if len(parts) != self.num_channels:
                return "Counter;"
            self.channels = [p.strip() == "1" for p in parts]
            return "OK;"

        return "Unknown CMD;"


@contextmanager
def patch_ibidi_serial(channels=24):
    """Patch ``serial.Serial`` as seen by ``IbidiMultiplexer`` with the fake.

    Yields the :class:`FakeIbidiSerial` instance the driver will use so the
    test can read back channel state and the command log.
    """
    fake = FakeIbidiSerial(channels=channels)

    def _factory(*args, **kwargs):
        fake.port = args[0] if args else kwargs.get("port", fake.port)
        fake.baudrate = kwargs.get("baudrate", fake.baudrate)
        fake.timeout = kwargs.get("timeout", fake.timeout)
        return fake

    with mock.patch(
        "PycroFlow.ibidi_multiplexer.Serial", side_effect=_factory
    ):
        yield fake


@contextmanager
def connect_multiplexer(port="COM-EMU", channels=24):
    """Yield a connected :class:`IbidiMultiplexer` backed by a fake serial."""
    from PycroFlow.ibidi_multiplexer import IbidiMultiplexer

    with patch_ibidi_serial(channels=channels) as fake:
        mx = IbidiMultiplexer(port, channels=channels)
        try:
            yield mx, fake
        finally:
            mx.close()
