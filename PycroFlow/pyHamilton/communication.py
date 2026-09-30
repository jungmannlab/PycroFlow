"""Serial communication with Hamilton PSD / MVP devices.

The Hamilton wire-protocol implementation lives in :class:`SerialBus`.
Module-level functions (``initializeSerial``, ``sendCommand`` etc.) are
preserved as thin shims so existing call sites continue to work; new code
should prefer ``SerialBus`` directly.

The module-level name ``abort_wait_response_flag`` remains an external
attachment point — :mod:`PycroFlow.hamilton_architecture` and
:mod:`PycroFlow.hamilton_components` assign a :class:`threading.Event` to
it to cancel ``waitForResponse`` mid-poll. ``SerialBus`` reads this name
at call time so external assignments stay effective.
"""

import sys
import threading
import time

import serial
from loguru import logger

# External cancellation hook. Set this to a threading.Event so a long-running
# waitForResponse() can be cancelled by setting the event from another
# thread. Left as None by default for back-compat.
abort_wait_response_flag = None

# Pump-status bytes returned in the second position of a Hamilton response.
# Used by waitForResponse to log human-readable status and to detect 'ready'.
STATUS_BYTES_INFO = {
    "@": "Pump is busy - no error",
    "`": "Pump is ready - no error",
    "a": "Initialization error – occurs when the pump fails to initialize",
    "b": "Invalid command – occurs when an unrecognized command is used.",
    "c": "Invalid operand – occurs when an invalid parameter is given with a command.",
    "d": "Invalid command sequence – occurs when the command communication protocol is incorrect",
    "f": "EEPROM failure – occurs when the EEPROM is faulty",
    "g": "Syringe not initialized – occurs when the syringe fails to initialize",
    "i": "Syringe overload – occurs when the syringe encounters excessive back pressure.",
    "j": "Valve overload – occurs when the valve drive encounters excessive back pressure.",
    "k": "Syringe move not allowed – when the valve is in the bypass or throughput position, syringe move commands are not allowed.",
    "o": "Pump is busy – occurs when the command buffer is full",
}

# Back-compat alias for code that previously imported statusBytesInfo.
statusBytesInfo = STATUS_BYTES_INFO


class SerialBus:
    """Owns a single Hamilton serial connection and serializes access to it.

    Replaces the previous module-level globals (``ser``, ``ComPort``,
    ``hamilton_comm_lock``). The instance is created lazily on first
    ``initialize`` and exposed via :func:`get_bus` for tests / advanced
    consumers.
    """

    def __init__(self, com_port_prefix="COM"):
        self.ser = None
        self.com_port_prefix = com_port_prefix
        self.lock = threading.Lock()

    def initialize(self, comm_port, baudrate):
        self.ser = serial.Serial()
        self.ser.port = self.com_port_prefix + str(comm_port)
        self.ser.baudrate = baudrate
        self.ser.bytesize = 8
        self.ser.parity = "N"
        self.ser.stopbits = 1
        self.ser.xonxoff = False
        self.ser.rtscts = False
        self.ser.dsrdtr = False
        self.ser.timeout = 10
        self.ser.open()
        if self.ser.isOpen():
            logger.debug("Open: " + self.ser.portstr)

    def disconnect(self):
        if self.ser is not None and self.ser.isOpen():
            self.ser.close()

    def encode_command(self, message):
        """Send ``message`` followed by CRLF, log the response. Does NOT
        serialize through the lock — call from contexts where you already
        hold it."""
        encoded = (message + "\r\n").encode()
        self.ser.write(encoded)
        respond_bytes = self.ser.readline()
        logger.debug("Response :" + respond_bytes.decode())

    def send_command(self, pump_address, message, wait_for_pump=False):
        command_header = "/" + pump_address
        command_footer = "\r\n"
        command = command_header + message + command_footer
        logger.debug("Sending command " + command)
        encoded_command = command.encode()

        with self.lock:
            self.ser.write(encoded_command)
            response_bytes = self.ser.readline()
            try:
                response = response_bytes.decode()
            except UnicodeDecodeError as exc:
                logger.exception(str(exc))
                response = ""

        if wait_for_pump:
            self.wait_for_response(command_header, command_footer)

        logger.debug("Response :" + response)
        return response

    def wait_for_response(self, header, footer):
        """Poll the pump until it reports 'ready' or the abort flag fires.

        The cancellation flag is read via the module-level name
        ``abort_wait_response_flag`` (assigned by
        ``hamilton_architecture._assign_multiprocess_events``), so external
        ``.set()`` calls take effect immediately.
        """
        logger.debug("Waiting for pump status ..")
        # Resolve the cancellation flag by module attribute so external
        # reassignments are seen each iteration.
        comm_module = sys.modules[__name__]
        while True:
            time.sleep(0.02)
            query = header + "QR" + footer
            with self.lock:
                self.ser.write(query.encode())
                respond_bytes = self.ser.readline()
            decoded = respond_bytes.decode()
            time.sleep(0.02)
            response_bit = decoded[2:3]
            if response_bit in STATUS_BYTES_INFO:
                logger.debug(
                    "Pump status: "
                    + response_bit
                    + " - "
                    + STATUS_BYTES_INFO[response_bit]
                )
                if response_bit == "`":
                    return
            flag = getattr(comm_module, "abort_wait_response_flag", None)
            if flag is not None and flag.is_set():
                logger.debug("waitForResponse aborted by external flag")
                return


# Module-level singleton. Lazily initialized; tests can substitute via
# ``set_bus(SerialBus())`` if needed.
_BUS = SerialBus()


def get_bus():
    """Return the module-level SerialBus singleton."""
    return _BUS


def set_bus(bus):
    """Replace the singleton (mainly for tests)."""
    global _BUS, ser, hamilton_comm_lock
    _BUS = bus
    ser = bus.ser
    hamilton_comm_lock = bus.lock


# Back-compat aliases for code that imported the old module globals.
# These mirror SerialBus internals after each operation that mutates them.
ser = _BUS.ser
ComPort = _BUS.com_port_prefix
hamilton_comm_lock = _BUS.lock


# --- Module-level shim functions preserved for back-compat ---------------


def initializeSerial(commPort, baudrate):
    _BUS.initialize(commPort, baudrate)
    # keep the legacy module global in sync for callers that read it
    global ser
    ser = _BUS.ser


def disconnectSerial():
    _BUS.disconnect()


def encodeCommand(message):
    _BUS.encode_command(message)


def sendCommand(pumpAddress, message, waitForPump=False):
    return _BUS.send_command(pumpAddress, message, wait_for_pump=waitForPump)


def waitForResponse(header, footer):
    _BUS.wait_for_response(header, footer)
