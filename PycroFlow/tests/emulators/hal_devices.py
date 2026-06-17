"""HAL-level hardware emulators implementing the ``hal`` ABCs.

Where :mod:`hamilton_serial` emulates the wire and exercises the real driver,
these emulators sit one layer up: they *are* :class:`~PycroFlow.hal.Pump` /
:class:`~PycroFlow.hal.Valve` / :class:`~PycroFlow.hal.SpillSensor`
implementations backed by plain Python state. Use them when a test needs a
working pump/valve/sensor but does not care about Hamilton bytes — e.g.
orchestration or fluid-logic tests, or to verify that a new consumer only
relies on the abstract contract.

Each device keeps a ``commands`` log of ``(method, kwargs)`` tuples so tests can
assert the sequence of operations without coupling to vendor command strings.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PycroFlow.hal import Pump, Valve, SpillSensor


class EmulatedPump(Pump):
    """An in-memory syringe pump.

    ``volume`` tracks the syringe contents in µL, clamped to
    ``[0, syringe_volume]``. ``pickup`` aspirates, ``dispense`` expels;
    over-/under-runs are clamped and recorded rather than raising, matching the
    forgiving behavior of the real firmware (which simply stalls).
    """

    def __init__(self, address='emu-pump', syringe_volume=500.0,
                 input_pos='in', output_pos='out', waste_pos=None):
        self.address = address
        self.syringe_volume = float(syringe_volume)
        self.input_pos = input_pos
        self.output_pos = output_pos
        self.waste_pos = waste_pos
        self.volume = 0.0
        self.target_volume = 0.0
        self.valve_pos = waste_pos
        self.last_velocity = None
        self.moving = False
        self.commands = []

    def _log(self, method, **kwargs):
        self.commands.append((method, kwargs))

    def pickup(self, vol, velocity=None, waitForPump=False,
               override_pause_flag=False):
        self._log('pickup', vol=vol, velocity=velocity, waitForPump=waitForPump)
        self.last_velocity = velocity
        self.target_volume = min(self.syringe_volume, self.target_volume + vol)
        if waitForPump:
            self.volume = self.target_volume
        else:
            self.moving = True

    def dispense(self, vol, velocity=None, waitForPump=False,
                 override_pause_flag=False):
        self._log('dispense', vol=vol, velocity=velocity,
                  waitForPump=waitForPump)
        self.last_velocity = velocity
        self.target_volume = max(0.0, self.target_volume - vol)
        if waitForPump:
            self.volume = self.target_volume
        else:
            self.moving = True

    def set_valve(self, pos, move_now=True):
        if pos == 'in':
            pos = self.input_pos
        elif pos == 'out':
            pos = self.output_pos
        self._log('set_valve', pos=pos, move_now=move_now)
        self.valve_pos = pos

    def wait_until_done(self):
        self._log('wait_until_done')
        self.volume = self.target_volume
        self.moving = False

    def stop_current_move(self):
        self._log('stop_current_move')
        # The real pump freezes wherever it is; reflect that by syncing the
        # target to the (unchanged) current volume.
        self.target_volume = self.volume
        self.moving = False

    def get_current_volume(self):
        return self.volume


class EmulatedValve(Valve):
    """An in-memory multi-position rotary valve."""

    def __init__(self, address='emu-valve', n_positions=8):
        self.address = address
        self.n_positions = n_positions
        self.position = None
        self.moving = False
        self.commands = []

    def set_valve(self, pos, move_now=True):
        self.commands.append(('set_valve', {'pos': pos, 'move_now': move_now}))
        if move_now:
            self.position = pos
            self.moving = False
        else:
            self.moving = True
            self._pending = pos

    def wait_until_done(self):
        self.commands.append(('wait_until_done', {}))
        if self.moving and hasattr(self, '_pending'):
            self.position = self._pending
        self.moving = False

    def get_status(self):
        return 'moving' if self.moving else 'idle@{}'.format(self.position)


class EmulatedSpillSensor(SpillSensor):
    """An in-memory wet/dry spill sensor.

    Drive its reading with :meth:`set_wet`. :meth:`monitor_sensor` starts a
    background thread (matching the Arduino implementation) that invokes
    ``fn_on_wet`` the first time a wet reading is seen.
    """

    def __init__(self, poll_interval=0.01):
        self._wet = False
        self._connected = False
        self.poll_interval = poll_interval
        self._abort = threading.Event()
        self._thread = None
        self.poll_count = 0

    # -- test-side control ----------------------------------------------------
    def set_wet(self, wet=True):
        self._wet = bool(wet)

    # -- SpillSensor contract -------------------------------------------------
    def connect(self):
        self._connected = True
        return True

    def disconnect(self):
        self.stop_monitoring()
        self._connected = False

    def poll_sensor(self):
        if not self._connected:
            return None
        self.poll_count += 1
        return self._wet

    def monitor_sensor(self, fn_on_wet: Optional[Callable[[str], None]] = None):
        self._abort.clear()

        def _worker():
            while not self._abort.is_set():
                if self._connected and self._wet:
                    if fn_on_wet is not None:
                        fn_on_wet('Spill sensor is wet.')
                    return
                if self._abort.wait(timeout=self.poll_interval):
                    return

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop_monitoring(self):
        self._abort.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._abort.clear()
