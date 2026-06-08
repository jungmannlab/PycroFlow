#!/usr/bin/env python
"""Orchestration framework for PycroFlow.

Coordinates fluid handling, image acquisition, and illumination subsystems
via per-subsystem handler threads synchronized through a shared
``threadexchange`` dict and a signal/wait-for-signal protocol.

Runnable REPL examples (using the demo protocols, no real hardware needed)::

    # Fluid-only orchestration
    from PycroFlow.examples.demo_protocols import protocol
    import PycroFlow.orchestration as por
    import PycroFlow.hamilton_architecture as ha

    prot = {'fluid': protocol['fluid']}
    ha.connect('18', 9600)
    la = ha.LegacyArchitecture(
        ha.legacy_system_config, ha.legacy_tubing_config, '18', 9600)
    po = por.ProtocolOrchestrator(prot, fluid_system=la)
    po.start_orchestration()
    po.start_protocol()

The matching imaging-only and combined fluid+imaging snippets live in
``PycroFlow/examples/demo_protocols.py``.

:authors: Heinrich Grabmayr, 2023
:copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""

import abc
import queue
import threading
import time
from functools import singledispatch

from loguru import logger

# Stage 4 typed-dispatch infrastructure. Import lazily-friendly names so a
# missing pydantic install during partial dev environments doesn't break
# the whole module — orchestration must remain importable for tests.
try:
    from PycroFlow.protocol_entries import (
        IncubateEntry,
        SignalEntry,
        WaitForSignalEntry,
        parse_entry,
    )

    _TYPED_ENTRIES_AVAILABLE = True
except Exception:
    _TYPED_ENTRIES_AVAILABLE = False


# Default timeout for inter-subsystem 'wait for signal' steps. Four hours
# covers the longest single Exchange-PAINT acquisition we run today; protocol
# entries may override per-step via a 'timeout' key. Raising
# WaitForSignalTimeout makes orchestration fail fast on YAML typos instead of
# hanging indefinitely (the old behavior).
WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT = 4 * 60 * 60  # seconds
WAIT_POLL_INTERVAL = 0.05


class WaitForSignalTimeout(RuntimeError):
    """Raised when a 'wait for signal' step exceeds its timeout."""


# Stage-4 typed dispatch table. ``functools.singledispatch`` selects a
# handler by the type of its first argument. Each ``register``ed function
# expects the typed entry plus the active ``AbstractSystemHandler`` and
# performs the side effect previously implemented inline in
# ``run_protocol`` as an if/elif on ``step['$type'].lower()``.
#
# The default (untyped raw dict) falls back to the subsystem's own
# ``execute_protocol_entry`` — same behaviour as the previous ``else``
# branch. This keeps any subsystem-specific entries that pydantic doesn't
# know about working without further changes.
@singledispatch
def dispatch_entry(entry, handler):
    """Default dispatcher.

    Delegate to the subsystem's ``execute_protocol_entry``.
    """
    handler.execute_protocol_entry(handler.protocol_iter)


if _TYPED_ENTRIES_AVAILABLE:

    @dispatch_entry.register
    def _(entry: SignalEntry, handler):
        handler.send_message(entry.value)

    @dispatch_entry.register
    def _(entry: WaitForSignalEntry, handler):
        handler.wait_xchange(entry.target, entry.value, timeout=entry.timeout)

    @dispatch_entry.register
    def _(entry: IncubateEntry, handler):
        tic = time.time()
        duration = float(entry.duration)
        while time.time() < tic + duration:
            if (
                handler.txchange["abort_flag"].is_set()
                or handler.txchange["abort_protocol_flag"].is_set()
            ):
                return
            time.sleep(0.05)


class AbstractSystem(abc.ABC):
    def __init__(self):
        pass

    @abc.abstractmethod
    def execute_protocol_entry(self, i):
        """execute protocol entry i"""
        pass

    @abc.abstractmethod
    def pause_execution(self):
        """Pause protocol execution"""
        pass

    @abc.abstractmethod
    def resume_execution(self):
        """Resume protocol execution after pausing"""
        pass

    @abc.abstractmethod
    def abort_execution(self):
        """Abort protocol execution"""
        pass


class AbstractSystemHandler(threading.Thread, abc.ABC):

    target = ""

    def __init__(self, protocol, threadexchange):
        # daemon=True so Ctrl-C or interpreter exit reliably terminates the
        # handler threads instead of leaving zombies that block the process.
        super().__init__(daemon=True)
        self.protocol = protocol
        logger.debug(
            "starting {:s} system handler with protocol {:s}".format(
                self.target, str(self.protocol)
            )
        )
        self.txchange = threadexchange
        self.system = None  # is set in Handler subclasses
        self.protocol_iter = 0

    def run(self):
        if self.system is None:
            logger.debug(f"setting {self.target + '_finished'} flag")
            self.txchange[self.target + "_finished"].set()
            return
        while (not self.txchange["abort_flag"].is_set()) and (
            not self.txchange["graceful_stop_flag"].is_set()
        ):
            if self.txchange["start_protocol_flag"].is_set():
                logger.debug("starting to run protocol")
                self.run_protocol()
                while not self.poll_protocol_finished():
                    if (
                        self.txchange["abort_flag"].is_set()
                        or self.txchange["abort_protocol_flag"].is_set()
                    ):
                        return
                    time.sleep(0.05)
                logger.debug("Clearing start_protocol_flag")
                self.txchange["start_protocol_flag"].clear()

            self.work_queue()
            time.sleep(0.02)

    def run_protocol(self):
        logger.debug(
            "start running protocol: {:s}".format(
                str(self.protocol["protocol_entries"])
            )
        )
        nsteps = len(self.protocol["protocol_entries"])
        self.protocol_iter = 0
        while self.protocol_iter < len(self.protocol["protocol_entries"]):
            step = self.protocol["protocol_entries"][self.protocol_iter]
            logger.debug(
                "System {:s} performing step {:d}/{:d}: {:s}".format(
                    self.target, self.protocol_iter + 1, nsteps, str(step)
                )
            )
            print(
                "System ",
                self.target,
                " performing step",
                self.protocol_iter + 1,
                "/",
                nsteps,
                ":",
                step,
            )
            # Stage-4 typed dispatch: try to parse the raw dict into the
            # matching pydantic model, then dispatch on its type via
            # ``functools.singledispatch``. If parsing fails (unknown $type,
            # malformed entry, or pydantic missing), fall back to the
            # legacy ``execute_protocol_entry`` path so subsystem-specific
            # entry types not enumerated in the schema still run.
            typed = None
            if _TYPED_ENTRIES_AVAILABLE:
                try:
                    typed = parse_entry(step)
                except Exception as exc:
                    logger.debug(
                        "typed-entry parse failed for step {!r}: {!r}; "
                        "falling back to execute_protocol_entry".format(
                            step, exc
                        )
                    )
            if typed is not None:
                dispatch_entry(typed, self)
            else:
                self.execute_protocol_entry(self.protocol_iter)

            self.protocol_iter += 1

            do_abort = self.housekeeping()
            # print('done housekeeping')
            if do_abort:
                self.send_message("Ending.")
                return

        logger.debug(f"setting {self.target + '_finished'} flag.")
        self.txchange[self.target + "_finished"].set()
        return

    def get_current_protocol_iter(self, arg=None):
        return self.protocol_iter

    def set_current_protocol_iter(self, i):
        self.protocol_iter = i

    def pause_protocol(self, msg=None):
        logger.debug(
            f"Setting pause flag from abstract system ({self.system})"
        )
        self.txchange["pause_protocol_flag"].set()

    def housekeeping(self):
        """Handle pause/resume/abort flags between protocol steps.

        Returns
        -------
        do_abort : bool
            Whether the protocol should abort.
        """
        pausing_protocol = False

        while True:
            if (
                self.txchange["abort_protocol_flag"].is_set()
                or self.txchange["abort_flag"].is_set()
            ):
                self.system.abort_execution()
                return True
            elif (self.txchange["pause_protocol_flag"].is_set()) and (
                not pausing_protocol
            ):
                # print(
                #     f"Abstract system housekeeping. Pause Protocol "
                #     f"Flag is set. System {self.system} is pausing")
                pausing_protocol = True
                self.system.pause_execution()
                continue
            elif (
                self.txchange["pause_protocol_flag"].is_set()
            ) and pausing_protocol:
                # continuing to pause
                continue
            elif (
                not self.txchange["pause_protocol_flag"].is_set()
            ) and pausing_protocol:
                # print(
                #     f"Abstract system housekeeping. Pause Protocol "
                #     f"Flag has been cleared. System {self.system} "
                #     f"will resume")
                pausing_protocol = False
                resumed = self.system.resume_execution()
                if resumed:
                    return False
                else:
                    # another pausing event occurred
                    continue
            else:
                # print("no condition met in housekeeping")
                time.sleep(0.05)
                return False

    def change_protocol_iteration(self, delta_iter):
        """Move the protocol iteration forward or backwards
        This is useful e.g. if a recording failed and should be recorded again
        """
        self.protocol_iter += delta_iter

    def wait_xchange(self, target, message, timeout=None):
        """Block until ``message`` appears on ``target``'s exchange list.

        Parameters
        ----------
        target : str
            The subsystem identifier ('fluid', 'img', 'illu') whose
            signal we're waiting for.
        message : str
            The signal value to look for.
        timeout : float or None
            Per-step deadline in seconds; falls back to
            :data:`WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT` when None. On expiry,
            :class:`WaitForSignalTimeout` is raised — previously a typo'd
            YAML hung the orchestrator forever.

        Raises
        ------
        WaitForSignalTimeout
            When the deadline expires before the signal arrives.

        Notes
        -----
        Returns silently on abort/protocol-iter change to preserve the
        existing cancellation semantics.

        Stage 4: the wait is now driven by
        :class:`PycroFlow.orchestration.signal_registry.SignalRegistry` —
        an ``Event``-backed lookup that returns the instant the matching
        signal fires. The legacy list-of-strings scan stays in the loop
        body as a fallback so callers that bypass ``send_message`` (e.g.
        ``start_protocol`` appending ``'start entry: 3'`` directly)
        continue to wake the waiter.
        """
        protocol_iter_begin = self.protocol_iter
        if timeout is None:
            timeout = WAIT_FOR_SIGNAL_TIMEOUT_DEFAULT
        deadline = time.monotonic() + float(timeout)

        # SignalRegistry may not exist on a hand-built threadexchange
        # (test stubs). Guard so the legacy path is used as a fallback.
        registry = (
            self.txchange.get("signal_registry")
            if hasattr(self.txchange, "get")
            else None
        )

        while True:
            if (
                self.txchange["abort_flag"].is_set()
                or self.txchange["abort_protocol_flag"].is_set()
                or protocol_iter_begin != self.protocol_iter
            ):
                return
            # Fast path: the registry fires the instant send_message runs.
            if registry is not None and registry.is_set(target, message):
                return
            # Legacy path: substring matches for 'target message' prefixed
            # entries that bypass send_message.
            with self.txchange[target + "_lock"]:
                msgs = self.txchange[target]
                if message in msgs or (target + " " + message) in msgs:
                    return
            if time.monotonic() >= deadline:
                raise WaitForSignalTimeout(
                    "{:s} timed out after {:.1f}s waiting for {!r} from "
                    "{!r}".format(self.target, float(timeout), message, target)
                )
            # Wait on the Event so we wake the instant a producer fires;
            # the per-poll deadline check above bounds the wait by
            # ``WAIT_POLL_INTERVAL`` so abort flags stay responsive.
            remaining = deadline - time.monotonic()
            block = min(WAIT_POLL_INTERVAL, max(remaining, 0.0))
            if registry is not None:
                registry.wait(target, message, timeout=block)
            else:
                time.sleep(block)

    def send_message(self, message):
        with self.txchange[self.target + "_lock"]:
            self.txchange[self.target].append(message)
        # Stage 4: also fire the signal-registry event so any handler
        # waiting via SignalRegistry wakes the instant we publish. We fire
        # under (self.target, message) — and, if the message carries the
        # legacy 'fluid round 1 done' / 'img round 1 done' prefix, also
        # under the stripped value — so the existing wait-for-signal
        # callers that look for either form pick it up.
        registry = (
            self.txchange.get("signal_registry")
            if hasattr(self.txchange, "get")
            else None
        )
        if registry is not None:
            registry.fire(self.target, message)
            prefix = self.target + " "
            if message.startswith(prefix):
                registry.fire(self.target, message[len(prefix) :])

    def search_message(self, substring):
        with self.txchange[self.target + "_lock"]:
            messages = self.txchange[self.target]
        for msg in messages:
            if substring in msg:
                return msg
        else:
            return None

    def poll_protocol_finished(self):
        events = [v for k, v in self.txchange.items() if "_finished" in k]
        finished = [ev.is_set() for ev in events]
        return all(finished)

    @abc.abstractmethod
    def work_queue(self):
        pass


class FluidHandler(AbstractSystemHandler):

    target = "fluid"

    def __init__(self, fluid_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = fluid_system
        if self.system is not None:
            # assign the protocol - restructure this later on
            self.system._assign_protocol(protocol)
            self.system.handler_ref = self
            self.abort_hamilton_wait_response_flag = threading.Event()
            self.system._assign_multiprocess_events(
                threadexchange["pause_protocol_flag"],
                threadexchange["abort_protocol_flag"],
                self.abort_hamilton_wait_response_flag,
            )

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + "_lock"]:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        try:
            item = self.txchange["fluid_queue"].get(timeout=0.05)
        except queue.Empty:
            item = None
        if item:
            if item["fun"] == "deliver":
                self.deliver_fluid(*item["args"], **item("kwargs"))

    def pause_protocol(self, msg=None):
        # print(f"Setting pause flag from abstract system ({self.system})")
        logger.debug("fluid handler setting protocol pausing flag")
        self.txchange["pause_protocol_flag"].set()
        logger.debug("setting hammilton wait flag")
        self.abort_hamilton_wait_response_flag.set()
        self.system.stop_all_moves()

    def resume_protocol(self, msg=None):
        # print(f"Setting pause flag from abstract system ({self.system})")
        logger.debug("fluid handler resuming protocol")
        logger.debug("clearing hamilton wait flag")
        self.abort_hamilton_wait_response_flag.clear()
        resumed = self.system.resume_execution()
        if resumed:
            logger.debug("finished the stopped execution")
        else:
            logger.debug("paused again during resuming")
        return resumed

    def abort_protocol(self, msg=None):
        # print(f"Setting pause flag from abstract system ({self.system})")
        logger.debug("fluid handler aborting protocol & setting abort flag")
        self.txchange["abort_protocol_flag"].set()
        logger.debug("setting hamilton wait flag")
        self.abort_hamilton_wait_response_flag.set()
        self.system.stop_all_moves()

    def deliver_fluid(self, reservoir_id, volume):
        """Deliver fluid of a given reservoir"""
        with self.txchange[self.target + "_lock"]:
            self.system.deliver_fluid(reservoir_id, volume)


class ImagingHandler(AbstractSystemHandler):

    target = "img"

    def __init__(self, imaging_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = imaging_system
        # Guard against None before touching attributes — previously
        # ``self.system.handler_ref = self`` ran unconditionally and
        # AttributeError'd on a None imaging system. The headless /
        # services-layer / GUI test paths legitimately construct
        # ProtocolOrchestrator without an imaging system.
        if self.system is not None:
            self.system.handler_ref = self
            self.system._assign_protocol(protocol)

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + "_lock"]:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        pass


class IlluminationHandler(AbstractSystemHandler):

    target = "illu"

    def __init__(self, illumination_system, protocol, threadexchange):
        super().__init__(protocol, threadexchange)
        self.system = illumination_system

    def execute_protocol_entry(self, i):
        with self.txchange[self.target + "_lock"]:
            self.system.execute_protocol_entry(i)

    def work_queue(self):
        pass


class ProtocolOrchestrator:
    """Takes a protocol and distributes the tasks to the different systems,
    waiting at signal boundaries via the per-instance
    :class:`PycroFlow.orchestration.threadexchange.ThreadExchange`.
    """

    def __init__(
        self,
        protocol,
        imaging_system=None,
        fluid_system=None,
        illumination_system=None,
    ):
        # Per-instance ThreadExchange — previously a class attribute, which
        # meant two ProtocolOrchestrator instances in the same process
        # aliased their locks and shared message lists. Stage 4 fixes that.
        from PycroFlow.orchestration.threadexchange import ThreadExchange

        self.threadexchange = ThreadExchange.create()

        self.fluid_system = fluid_system
        self.fluid_handler = FluidHandler(
            fluid_system, protocol.get("fluid", []), self.threadexchange
        )

        self.imaging_system = imaging_system
        self.imaging_handler = ImagingHandler(
            imaging_system, protocol.get("img", []), self.threadexchange
        )

        self.illumination_system = illumination_system
        self.illumination_handler = IlluminationHandler(
            illumination_system, protocol.get("illu", []), self.threadexchange
        )

        self.protocol = protocol

    def start_orchestration(self):
        self.fluid_handler.start()
        self.imaging_handler.start()
        self.illumination_handler.start()

    def start_protocol(self, system_steps={}):
        """Start the protocol, optionally seeding per-system start steps.

        Parameters
        ----------
        system_steps : dict
            Sets the start steps of various systems. Keys are subsystem
            identifiers (e.g. ``'fluid'``, ``'img'``, ``'illu'``); values
            are the integer start step for that system.
        """
        if system_steps != {}:
            for syst, step in system_steps.items():
                with self.threadexchange[syst + "_lock"]:
                    self.threadexchange[syst].append(f"start entry: {step}")

        logger.debug("setting start protocol flag")
        self.threadexchange["start_protocol_flag"].set()

    def abort_protocol(self):
        logger.debug("setting abort protocol flag")
        self.threadexchange["abort_protocol_flag"].set()
        self.fluid_handler.abort_protocol()

    def pause_protocol(self):
        logger.debug("orchestrator pausing protocol & setting pause flag")
        self.threadexchange["pause_protocol_flag"].set()
        self.fluid_handler.pause_protocol()

    def resume_protocol(self):
        # print("orchestrator resuming protocol. clearing pause flag.")
        resumed = self.fluid_handler.resume_protocol()
        if resumed:
            logger.debug("Successfully resumed. clearing pause protocol flag")
            self.threadexchange["pause_protocol_flag"].clear()
        else:
            logger.debug(
                "Paused again during resuming. not clearing pause flag."
            )

    def _join_handlers(self):
        """Join only handlers that were actually started.

        The handler threads are created in ``__init__`` but not started until
        :meth:`start_orchestration`. ``Thread.join()`` raises
        ``RuntimeError('cannot join thread before it is started')`` on a
        never-started thread, so abort/end (incl. the ``__del__`` path) on an
        orchestrator that was constructed but never run would otherwise raise.
        ``is_alive()`` is False for both never-started and already-finished
        threads; joining a finished thread is a no-op, so skipping it is safe.
        """
        for handler in (
            self.fluid_handler,
            self.imaging_handler,
            self.illumination_handler,
        ):
            if handler.is_alive():
                handler.join()

    def abort_orchestration(self):
        logger.debug("setting abort flag")
        self.threadexchange["abort_flag"].set()
        self._join_handlers()

    def poll_protocol_finished(self):
        events = [
            v for k, v in self.threadexchange.items() if "_finished" in k
        ]
        finished = [ev.is_set() for ev in events]
        return all(finished)

    def end_orchestration(self):
        logger.debug("setting graceful stop flag")
        self.threadexchange["graceful_stop_flag"].set()
        self._join_handlers()

    def enqueue_fluid_function(self, function, args, kwargs):
        self.threadexchange["fluid_queue"].put(
            {"fun": function, "args": args, "kwargs": kwargs}
        )

    def execute_system_function(self, target, fun, args=[], kwargs={}):
        """Execute a function of a target system (e.g. Hamilton fluid system).

        This should be done via this function instead of directly because the
        system is also called by another thread and should therefore only be
        accessed within a lock.

        Parameters
        ----------
        target : str
            The target (e.g. ``'fluid'``).
        fun : callable
            The function to call (e.g. ``self.fluid_system._pump``).
        args : list
            The positional arguments to the function.
        kwargs : dict
            The keyword arguments to the function.

        Returns
        -------
        object
            The return value of ``fun``.
        """
        with self.threadexchange[target + "_lock"]:
            result = fun(*args, **kwargs)
        return result

    def __del__(self):
        # __del__ runs during interpreter shutdown; swallow but log so we can
        # diagnose failed abort paths from the log without crashing GC.
        try:
            self.abort_orchestration()
        except Exception as e:
            try:
                logger.warning(
                    "ProtocolOrchestrator.__del__ failed to abort "
                    "cleanly: {!r}".format(e)
                )
            except Exception:
                pass
