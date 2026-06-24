"""ibidi MultiFlOW actuator unit (24-channel multiplexer) driver.

The ibidi *MultiFlOW Actuator Unit* is a standalone USB device controlling 24
bi-stable on/off valves over a USB CDC (serial) link. It is an alternative to
the Hamilton MVP rotary valves for multiplexing which reservoir feeds the
syringe pump: instead of one rotary valve cycling through N positions, the
multiplexer opens exactly one of its 24 channels.

This driver speaks the firmware command set (see ``MultiFlOW_Commands_FW``):

======================================  =======================================
command                                 response
======================================  =======================================
``ID;`` / ``*IDN?;``                    ``MX;``
``FWV;``                                firmware version, e.g. ``0.0.2;``
``HWV;``                                hardware version
``CH;``                                 channel count, ``24;``
``SETALL;``                             all 24 valves ON -> ``OK;``
``UNSETALL;``                           all 24 valves OFF -> ``OK;``
``SET:Valve:<v>:<s>;``                  one valve (1-24) to state (0/1)
``SETBATCHVALVES=<24 csv bits>;``       all valves at once (``1,0,...,0``)
``CLRFLT;``                             clear latched DRV8912 faults
``RDSTAT=<idx>;``                       read one DRV8912 status register
``RDALLSTAT;``                          dump all registers (debug)
======================================  =======================================

To behave as a drop-in for :class:`PycroFlow.hamilton_components.Valve` (so the
existing :meth:`LegacyArchitecture._set_valves` works unchanged), the class
exposes :meth:`set_valve` / :meth:`wait_until_done` / :meth:`get_status` and
the ``pause_flag`` / ``abort_flag`` events the orchestrator assigns.
``set_valve``
selects a single channel *exclusively* (all others closed) via one atomic
``SETBATCHVALVES`` command, which is the correct routing for reservoir
multiplexing.
"""
from __future__ import annotations

import threading
import time

# Bind ``Serial`` directly into this module's namespace (rather than using
# ``serial.Serial``) so it can be patched independently of the Hamilton bus,
# which patches the shared ``serial.Serial`` global. See the emulator's
# ``patch_ibidi_serial``.
from serial import Serial
from loguru import logger

from PycroFlow.hal.valves import Valve as _ValveABC

DEVICE_ID = "MX"
TERMINATOR = ";"


class IbidiMultiplexer(_ValveABC):
    """Driver for the ibidi MultiFlOW 24-channel valve actuator.

    Parameters
    ----------
    port : str
        Serial port the device enumerates as (e.g. ``'COM7'`` or
        ``'/dev/ttyACM0'``). A bare number / ``'COM<n>'`` is accepted.
    baud : int, default: 115200
        Baud rate (the device runs 115200 8N1).
    channels : int, default: 24
        Number of controllable channels.
    timeout : float, default: 2
        Serial read timeout in seconds.
    address : str, default: 'ibidi'
        Key under which the multiplexer is registered in the fluid system's
        valve map; also the key used in a reservoir's ``valve_pos`` mapping.
    line_ending : str, default: ''
        Extra bytes appended after the ``;``-terminated command (some CDC
        stacks want ``'\\r\\n'``); empty by default.
    connect : bool, default: True
        Open the serial port and verify the device id on construction. Pass
        ``False`` to build the object without I/O (mainly for tests).
    """

    def __init__(
        self,
        port,
        baud=115200,
        channels=24,
        timeout=2,
        address="ibidi",
        line_ending="",
        connect=True,
        **kwargs,
    ):
        self.address = address
        self.channels = int(channels)
        self.line_ending = line_ending
        self._lock = threading.Lock()
        self._serial = None

        # Default to private, never-set events so direct hardware use before
        # orchestration starts does not dereference a None flag. The
        # orchestrator later swaps in its shared events via
        # ``LegacyArchitecture._assign_multiprocess_events``.
        self.pause_flag = threading.Event()
        self.abort_flag = threading.Event()

        # Last commanded channel states (1-based index -> bool), best effort.
        self.channel_states = [False] * self.channels

        if connect:
            self.connect(port, baud, timeout)

    # -- connection -----------------------------------------------------------
    def connect(self, port, baud=115200, timeout=2):
        """Open the serial port and confirm the device identifies as ``MX``."""
        port = self._normalize_port(port)
        self._serial = Serial(port, baudrate=baud, timeout=timeout)
        ident = self.identify()
        if DEVICE_ID not in ident:
            logger.warning(
                "ibidi multiplexer on {} returned unexpected id {!r} "
                "(expected to contain {!r})".format(port, ident, DEVICE_ID)
            )
        else:
            logger.info(
                "ibidi multiplexer connected on {} ({} channels)".format(
                    port, self.channels
                )
            )

    @staticmethod
    def _normalize_port(port):
        port = str(port)
        if port.isdigit():
            return "COM" + port
        return port

    def close(self):
        """Close the serial connection. Safe to call repeatedly."""
        if self._serial is not None:
            try:
                if getattr(self._serial, "is_open", True):
                    self._serial.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("ibidi close failed: {!r}".format(exc))
            self._serial = None

    def __del__(self):  # pragma: no cover - GC timing dependent
        self.close()

    # -- low-level command ----------------------------------------------------
    def _command(self, cmd):
        """Send one ``;``-terminated command and return the stripped reply."""
        if self._serial is None:
            raise RuntimeError("ibidi multiplexer is not connected")
        if not cmd.endswith(TERMINATOR):
            cmd = cmd + TERMINATOR
        payload = (cmd + self.line_ending).encode()
        with self._lock:
            reset = getattr(self._serial, "reset_input_buffer", None)
            if callable(reset):
                reset()
            self._serial.write(payload)
            raw = self._serial.read_until(TERMINATOR.encode())
        reply = raw.decode(errors="replace").strip()
        logger.debug("ibidi {!r} -> {!r}".format(cmd, reply))
        return reply

    @staticmethod
    def _ok(reply):
        return reply.replace(";", "").strip().upper() == "OK"

    # -- identification / status ---------------------------------------------
    def identify(self):
        """Return the device identifier (expected ``'MX;'``)."""
        return self._command("ID")

    def firmware_version(self):
        """Return the firmware version string."""
        return self._command("FWV")

    def hardware_version(self):
        """Return the hardware version string."""
        return self._command("HWV")

    def num_channels(self):
        """Query and return the device's channel count as an int."""
        reply = self._command("CH")
        try:
            return int(reply.replace(";", "").strip())
        except ValueError:
            return self.channels

    def get_status(self):
        """Return a status descriptor (the device id), for connection checks.

        Mirrors :meth:`PycroFlow.hamilton_components.Valve.get_status`: a
        non-empty return means the device is responsive.
        """
        try:
            return self.identify()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("ibidi get_status failed: {!r}".format(exc))
            return ""

    def clear_faults(self):
        """Clear all latched fault bits on both DRV8912 chips (``CLRFLT``)."""
        return self._command("CLRFLT")

    def read_status(self, drv_idx):
        """Read the status register of DRV8912 chip ``drv_idx`` (0 or 1)."""
        return self._command("RDSTAT={}".format(int(drv_idx)))

    # -- channel control ------------------------------------------------------
    def set_all(self):
        """Turn all channels ON (``SETALL``)."""
        reply = self._command("SETALL")
        if self._ok(reply):
            self.channel_states = [True] * self.channels
        return reply

    def unset_all(self):
        """Turn all channels OFF (``UNSETALL``)."""
        reply = self._command("UNSETALL")
        if self._ok(reply):
            self.channel_states = [False] * self.channels
        return reply

    def set_channel(self, channel, state):
        """Set a single channel (1-based) ON/OFF (``SET:Valve:<v>:<s>``)."""
        self._check_channel(channel)
        bit = 1 if state else 0
        reply = self._command("SET:Valve:{}:{}".format(channel, bit))
        if self._ok(reply):
            self.channel_states[channel - 1] = bool(state)
        else:
            raise RuntimeError(
                "ibidi SET:Valve:{}:{} failed: {!r}".format(
                    channel, bit, reply
                )
            )
        return reply

    def set_batch(self, states):
        """Set all channels at once from a sequence of 24 truthy/falsy values.

        Sends one ``SETBATCHVALVES=<csv>`` command (atomic on the device).
        """
        states = list(states)
        if len(states) != self.channels:
            raise ValueError(
                "expected {} channel states, got {}".format(
                    self.channels, len(states)
                )
            )
        csv = ",".join("1" if s else "0" for s in states)
        reply = self._command("SETBATCHVALVES={}".format(csv))
        if not self._ok(reply):
            raise RuntimeError(
                "ibidi SETBATCHVALVES failed: {!r}".format(reply)
            )
        self.channel_states = [bool(s) for s in states]
        return reply

    def select(self, channel):
        """Open one channel (1-based), closing all others atomically."""
        self._check_channel(channel)
        states = [False] * self.channels
        states[channel - 1] = True
        return self.set_batch(states)

    def _check_channel(self, channel):
        if not (1 <= int(channel) <= self.channels):
            raise ValueError(
                "channel {!r} out of range 1..{}".format(
                    channel, self.channels
                )
            )

    # -- HAL Valve interface --------------------------------------------------
    def set_valve(self, pos, move_now=True):
        """Route to reservoir channel ``pos`` (1-based), closing all others.

        Presents the same surface as the Hamilton MVP ``Valve`` so
        :meth:`LegacyArchitecture._set_valves` drives it unchanged. Honours the
        ``pause_flag`` / ``abort_flag`` the orchestrator assigns: blocks while
        paused, and skips the move entirely if aborted.

        Parameters
        ----------
        pos : int
            The channel to open (1..``channels``).
        move_now : bool, default: True
            Accepted for interface compatibility; the device switches
            immediately, so this is ignored.
        """
        i = 0
        while self.pause_flag.is_set():
            if i == 0:
                logger.debug(
                    "Pause flag set; ibidi multiplexer {} waiting to "
                    "switch.".format(self.address)
                )
            i += 1
            if self.abort_flag.is_set():
                logger.debug(
                    "Abort flag set; ibidi multiplexer {} not "
                    "switching.".format(self.address)
                )
                return
            time.sleep(0.02)
        if self.abort_flag.is_set():
            logger.debug(
                "Abort flag set; ibidi multiplexer {} not switching.".format(
                    self.address
                )
            )
            return
        self.select(int(pos))

    def wait_until_done(self):
        """No-op: bi-stable valves switch synchronously (the device replies
        ``OK`` only once the command has been applied)."""
        return

    def __repr__(self):  # pragma: no cover - cosmetic
        return "IbidiMultiplexer(address={!r}, channels={})".format(
            self.address, self.channels
        )
