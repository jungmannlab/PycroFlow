"""Serial-level emulator for the Hamilton PSD / MVP multi-drop bus.

This is the lowest-level hardware emulator: it presents the same surface as a
``serial.Serial`` instance, so the *real* :class:`PycroFlow.pyHamilton.SerialBus`,
:class:`PycroFlow.hamilton_components.Pump` and ``Valve`` code runs end to end
against it without modification. That gives genuine coverage of the wire-protocol
encode/decode path that a ``MagicMock`` on ``sendCommand`` cannot.

The Hamilton wire protocol (see ``pyHamilton/communication.py``):

* A command is ``/<addr><message>\\r\\n`` where ``<addr>`` is the device's ASCII
  address (``'1'``..``'@'``, see ``PSD.setAddress``).
* A reply is ``/0<status><data><ETX>`` where ``<status>`` is ``'`'`` (ready) or
  ``'@'`` (busy), ``<data>`` is the query payload, and ``ETX`` is ``\\x03``.
  ``'0'`` is the host/master address.

Several devices share one bus; the emulator keeps per-address state in
:class:`EmulatedHamiltonDevice` and routes each command by the address byte.

Typical use in a test::

    from PycroFlow.tests.emulators import hamilton_serial as hs

    with hs.patch_serial() as fake:
        ham.connect('18', 9600)
        pump = Pump('2', '500u', instrument_type='4', valve_type='Y',
                    output_pos='out', input_pos='in', waste_pos=1,
                    pause_flag=ev, abort_flag=ev)
        pump.pickup(250, waitForPump=True)
        assert abs(pump.get_current_volume() - 250) < 1.0
        assert fake.device('3').syringe_steps > 0   # ascii addr of pump '2'
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from unittest import mock

ETX = "\x03"
STATUS_READY = "`"
STATUS_BUSY = "@"

# Opcode patterns applied to the message body (after the leading '/<addr>').
_RE_PICKUP = re.compile(r"P(\d+)")
_RE_DISPENSE = re.compile(r"D(\d+)")
_RE_ABS_MOVE = re.compile(r"A(\d+)")
# Move-valve-in-shortest-direction: h2600<pos>; clockwise h2400, ccw h2500.
_RE_VALVE_SHORTEST = re.compile(r"h2[456]00(\d)")


class EmulatedHamiltonDevice:
    """In-memory state of a single PSD pump or MVP valve on the bus.

    Step bookkeeping is deliberately faithful: ``syringe_steps`` accumulates the
    exact integer step counts written on the wire by the real ``Pump`` code, so
    a ``pickup``/``dispense`` followed by ``get_current_volume`` round-trips to
    the requested volume (the host converts steps<->µL with the same scale).
    """

    def __init__(self, address):
        self.address = address
        self.syringe_steps = 0
        self.valve_pos = None
        # Reported back for the syringe-mode query; the host parses position
        # [3] of the reply as the resolution mode and derives the step scale
        # (high-resolution PSD4 -> 24000 steps full stroke).
        self.resolution_mode = 1
        self.initialized = False
        self.last_command = None
        # Every (message, response) pair this device handled, for assertions.
        self.history = []

    def handle(self, message):
        """Apply ``message`` to the device state and return the reply body
        (the ``<status><data>`` portion, without the leading ``/0`` or ETX)."""
        self.last_command = message

        # --- Queries ---------------------------------------------------------
        if message == "?":  # absolute syringe position
            data = STATUS_READY + str(self.syringe_steps)
        elif message.startswith("?11000"):  # syringe-mode query
            # Host reads reply[3] as the resolution digit, so the payload must
            # start with the mode digit.
            data = STATUS_READY + str(self.resolution_mode)
        elif message.startswith("?"):  # other queries: benign ready + 0
            data = STATUS_READY + "0"
        elif message.startswith("Q"):  # pump/valve status query (incl. 'QR')
            data = STATUS_READY
        else:
            # --- Action commands --------------------------------------------
            self._apply_action(message)
            data = STATUS_READY

        response_body = data
        self.history.append((message, response_body))
        return response_body

    def _apply_action(self, message):
        if "Z" in message or "Y" in message or "h20000" in message:
            self.initialized = True

        m = _RE_VALVE_SHORTEST.search(message)
        if m:
            self.valve_pos = int(m.group(1))

        for m in _RE_PICKUP.finditer(message):
            self.syringe_steps += int(m.group(1))
        for m in _RE_DISPENSE.finditer(message):
            self.syringe_steps = max(0, self.syringe_steps - int(m.group(1)))
        for m in _RE_ABS_MOVE.finditer(message):
            self.syringe_steps = int(m.group(1))


class FakeHamiltonSerial:
    """Drop-in replacement for ``serial.Serial`` speaking the Hamilton protocol.

    Instances are created with no arguments (matching ``serial.Serial()`` as
    called by :meth:`SerialBus.initialize`); the bus then assigns ``port``,
    ``baudrate`` etc. as attributes and calls :meth:`open`. Per-device state is
    created lazily on first contact and exposed via :meth:`device`.
    """

    def __init__(self, *args, **kwargs):
        self._open = False
        self._rx = bytearray()  # bytes waiting to be readline()'d
        self._lock = threading.Lock()
        self.devices = {}
        # (address, message) for every command seen, across all devices.
        self.command_log = []
        # serial.Serial attributes the bus sets; default them so attribute
        # access never raises even before assignment.
        self.port = None
        self.baudrate = None
        self.timeout = None

    # -- serial.Serial surface ------------------------------------------------
    def open(self):
        self._open = True

    def isOpen(self):
        return self._open

    @property
    def is_open(self):
        return self._open

    @property
    def portstr(self):
        return str(self.port)

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
        """Parse one command frame and queue its reply for the next readline."""
        text = data.decode() if isinstance(data, (bytes, bytearray)) else data
        for frame in self._split_frames(text):
            reply = self._handle_frame(frame)
            with self._lock:
                self._rx.extend(reply.encode())
        return len(data)

    def readline(self):
        with self._lock:
            idx = self._rx.find(b"\n")
            if idx == -1:
                line, self._rx = bytes(self._rx), bytearray()
            else:
                line = bytes(self._rx[: idx + 1])
                del self._rx[: idx + 1]
        return line

    def read(self, size=1):
        with self._lock:
            chunk = bytes(self._rx[:size])
            del self._rx[:size]
        return chunk

    # -- emulator helpers -----------------------------------------------------
    def device(self, address):
        """Return (creating if needed) the device state for an ASCII address."""
        if address not in self.devices:
            self.devices[address] = EmulatedHamiltonDevice(address)
        return self.devices[address]

    @staticmethod
    def _split_frames(text):
        # Commands are CRLF-terminated; a single write carries exactly one, but
        # be tolerant of batching.
        return [f for f in re.split(r"\r\n", text) if f]

    def _handle_frame(self, frame):
        if not frame.startswith("/"):
            # Unaddressed/garbage frame: reply ready so callers don't hang.
            return "/0" + STATUS_READY + ETX + "\r\n"
        address = frame[1]
        message = frame[2:]
        self.command_log.append((address, message))
        body = self.device(address).handle(message)
        return "/0" + body + ETX + "\r\n"


@contextmanager
def patch_serial():
    """Patch ``serial.Serial`` (as seen by ``SerialBus``) with the emulator.

    Yields the :class:`FakeHamiltonSerial` instance the bus will actually use,
    so the test can read back device state and the command log. The bus is a
    module-level singleton, so its ``.ser`` is replaced on ``connect``.
    """
    fake = FakeHamiltonSerial()

    def _factory(*args, **kwargs):
        return fake

    with mock.patch(
        "PycroFlow.pyHamilton.communication.serial.Serial",
        side_effect=_factory,
    ):
        yield fake


def make_fake_bus():
    """Return a :class:`SerialBus` already wired to a fresh emulator.

    Useful for tests that prefer ``ham.communication.set_bus(make_fake_bus())``
    over patching the ``serial`` import. The returned bus reports as open.
    """
    from PycroFlow.pyHamilton.communication import SerialBus

    bus = SerialBus()
    bus.ser = FakeHamiltonSerial()
    bus.ser.open()
    return bus
