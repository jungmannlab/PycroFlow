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

**Wire polarity is inverted** relative to what "set" suggests: a valve is
*open* (flowing) when its bit is ``0`` and *closed* when it is ``1``
(:data:`WIRE_OPEN` / :data:`WIRE_CLOSED`). This module's API — ``select``,
``set_channel``, ``open_all`` / ``close_all``, :attr:`channel_states` — always
speaks in terms of **open**, and inverts at the wire, so nothing above the
driver has to remember the encoding.

To behave as a drop-in for :class:`PycroFlow.hamilton_components.Valve` (so the
existing :meth:`LegacyArchitecture._set_valves` works unchanged), the class
exposes :meth:`set_valve` / :meth:`wait_until_done` / :meth:`get_status` and
the ``pause_flag`` / ``abort_flag`` events the orchestrator assigns.
``set_valve`` opens the requested channel(s) *exclusively* (all others closed),
which is the correct routing for reservoir multiplexing.

The two valve kinds differ in principle: a Hamilton rotary valve is at exactly
one position at a time, whereas these 24 valves are independent and several
may be open together. A reservoir's ``valve_pos`` may therefore name either a
single channel (``{ibidi: 3}``) or a list (``{ibidi: [1, 3]}``); in both cases
every channel *not* listed is closed.

Switching **one valve at a time** is the default (``batch_valves=False``).
``SETBATCHVALVES`` actuates every changing valve simultaneously, whose inrush
current exceeds what the unit can supply, so some valves silently fail to
move. The sequential path issues one ``SET:Valve`` per channel spaced by
``switch_delay`` seconds. Set ``batch_valves=True`` to go back to the single
atomic command once the hardware supports it.
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

#: Wire encoding of a valve state: the bit is inverted with respect to flow.
#: ``SET:Valve:<v>:0`` *opens* the valve; ``:1`` closes it.
WIRE_OPEN = 0
WIRE_CLOSED = 1

#: Seconds between consecutive single-valve commands when switching
#: sequentially, so their actuation currents do not overlap.
DEFAULT_SWITCH_DELAY = 0.01


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
    batch_valves : bool, default: False
        Switch every valve with one atomic ``SETBATCHVALVES``. Off by
        default: the simultaneous inrush current exceeds what the unit can
        supply, so some valves do not actuate. Turn back on when the
        hardware can drive it — the routing is identical either way.
    switch_delay : float, default: 0.01
        Seconds between consecutive single-valve commands in the sequential
        path, spreading the actuation current. Ignored when batching.
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
        batch_valves=False,
        switch_delay=DEFAULT_SWITCH_DELAY,
        **kwargs,
    ):
        self.address = address
        self.channels = int(channels)
        self.line_ending = line_ending
        self.batch_valves = bool(batch_valves)
        self.switch_delay = float(switch_delay)
        self._lock = threading.Lock()
        self._serial = None

        # Default to private, never-set events so direct hardware use before
        # orchestration starts does not dereference a None flag. The
        # orchestrator later swaps in its shared events via
        # ``LegacyArchitecture._assign_multiprocess_events``.
        self.pause_flag = threading.Event()
        self.abort_flag = threading.Event()

        # Last commanded channel states, best effort: index i holds whether
        # channel i+1 is OPEN (flowing), not the inverted wire bit.
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
    def open_all(self):
        """Open every channel, one valve at a time (or batched)."""
        return self.apply_states([True] * self.channels)

    def close_all(self):
        """Close every channel, one valve at a time (or batched)."""
        return self.apply_states([False] * self.channels)

    def set_all(self):
        """Send the raw ``SETALL`` firmware command.

        Kept as a firmware pass-through. Prefer :meth:`close_all` /
        :meth:`open_all`: only the per-valve polarity is confirmed
        (:data:`WIRE_OPEN`), and this actuates all 24 valves at once — the
        very thing the unit's supply cannot sustain. The resulting state is
        therefore recorded as unknown.
        """
        reply = self._command("SETALL")
        self._invalidate_states("SETALL")
        return reply

    def unset_all(self):
        """Send the raw ``UNSETALL`` firmware command (see :meth:`set_all`)."""
        reply = self._command("UNSETALL")
        self._invalidate_states("UNSETALL")
        return reply

    def _invalidate_states(self, command):
        """Forget the cached states after a command with unverified effect."""
        logger.debug(
            "ibidi {} sent; cached channel states are now unknown until the "
            "next select()".format(command))
        self.channel_states = [None] * self.channels

    def set_channel(self, channel, open_):
        """Open or close a single channel (``SET:Valve:<v>:<s>``).

        Parameters
        ----------
        channel : int
            Channel to actuate (1..``channels``).
        open_ : bool
            True to open (let liquid through), False to close. Inverted onto
            the wire — the device opens on ``0`` (:data:`WIRE_OPEN`).
        """
        self._check_channel(channel)
        bit = WIRE_OPEN if open_ else WIRE_CLOSED
        reply = self._command("SET:Valve:{}:{}".format(channel, bit))
        if self._ok(reply):
            self.channel_states[channel - 1] = bool(open_)
        else:
            raise RuntimeError(
                "ibidi SET:Valve:{}:{} failed: {!r}".format(
                    channel, bit, reply
                )
            )
        return reply

    def set_batch(self, states):
        """Set all channels at once from a sequence of open/closed flags.

        Sends one ``SETBATCHVALVES=<csv>`` command (atomic on the device),
        with the bits inverted (:data:`WIRE_OPEN`). Note the hardware caveat
        in the module docstring: actuating many valves simultaneously draws
        more current than the unit supplies, so some may not move. Reach it
        through :meth:`apply_states` rather than calling it directly.

        Parameters
        ----------
        states : sequence of bool
            One flag per channel; True = open.
        """
        states = list(states)
        if len(states) != self.channels:
            raise ValueError(
                "expected {} channel states, got {}".format(
                    self.channels, len(states)
                )
            )
        csv = ",".join(
            str(WIRE_OPEN if s else WIRE_CLOSED) for s in states)
        reply = self._command("SETBATCHVALVES={}".format(csv))
        if not self._ok(reply):
            raise RuntimeError(
                "ibidi SETBATCHVALVES failed: {!r}".format(reply)
            )
        self.channel_states = [bool(s) for s in states]
        return reply

    def apply_states(self, states):
        """Drive every channel to ``states`` (True = open).

        Uses one atomic ``SETBATCHVALVES`` when ``batch_valves`` is set,
        otherwise switches one valve at a time — see the module docstring
        for why sequential is the default.

        Parameters
        ----------
        states : sequence of bool
            One flag per channel; True = open.

        Returns
        -------
        str
            The reply of the last command sent.
        """
        states = list(states)
        if len(states) != self.channels:
            raise ValueError(
                "expected {} channel states, got {}".format(
                    self.channels, len(states)))
        if self.batch_valves:
            return self.set_batch(states)
        return self._apply_states_sequentially(states)

    def _apply_states_sequentially(self, states):
        """Switch one valve at a time, closing before opening.

        Closing first means no moment where an old feed path and the new one
        are open together, and spacing the commands by ``switch_delay``
        keeps the actuation currents from overlapping.
        """
        order = [(ch, False) for ch, s in enumerate(states, 1) if not s]
        order += [(ch, True) for ch, s in enumerate(states, 1) if s]
        reply = ""
        for i, (channel, open_) in enumerate(order):
            if i and self.switch_delay > 0:
                time.sleep(self.switch_delay)
            reply = self.set_channel(channel, open_)
        return reply

    def select(self, channel):
        """Open one channel or a set of channels, closing all others.

        Unlike a Hamilton rotary valve — which is at exactly one position at
        a time — the multiplexer's 24 valves are independent, so a reservoir
        may be reached through several channels open at once (e.g. a shared
        feed line). Passing a sequence opens exactly those and closes every
        other channel — one valve at a time by default, see
        :meth:`apply_states`.

        Parameters
        ----------
        channel : int or sequence of int
            The channel(s) to open (1..``channels``). A sequence must be
            non-empty; duplicates are ignored.

        Returns
        -------
        str
            The device reply of the last command sent.
        """
        channels = self._as_channels(channel)
        states = [False] * self.channels
        for ch in channels:
            states[ch - 1] = True
        return self.apply_states(states)

    def _as_channels(self, channel):
        """Normalise an int / sequence of ints to a validated channel list."""
        if isinstance(channel, (list, tuple, set, frozenset)):
            channels = [int(c) for c in channel]
            if not channels:
                raise ValueError(
                    "no channel given; a reservoir's ibidi valve_pos must "
                    "name at least one channel")
        else:
            channels = [int(channel)]
        for ch in channels:
            self._check_channel(ch)
        return channels

    def _check_channel(self, channel):
        if not (1 <= int(channel) <= self.channels):
            raise ValueError(
                "channel {!r} out of range 1..{}".format(
                    channel, self.channels
                )
            )

    # -- HAL Valve interface --------------------------------------------------
    def set_valve(self, pos, move_now=True):
        """Route to reservoir channel(s) ``pos``, closing all others.

        Presents the same surface as the Hamilton MVP ``Valve`` so
        :meth:`LegacyArchitecture._set_valves` drives it unchanged. Honours the
        ``pause_flag`` / ``abort_flag`` the orchestrator assigns: blocks while
        paused, and skips the move entirely if aborted.

        Parameters
        ----------
        pos : int or sequence of int
            The channel(s) to open (1..``channels``). A Hamilton valve takes
            a single position; this device can hold several open at once, so
            a reservoir's ``valve_pos`` may name a list (see :meth:`select`).
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
        self.select(pos)

    def wait_until_done(self):
        """No-op: bi-stable valves switch synchronously (the device replies
        ``OK`` only once the command has been applied)."""
        return

    def __repr__(self):  # pragma: no cover - cosmetic
        return "IbidiMultiplexer(address={!r}, channels={})".format(
            self.address, self.channels
        )
