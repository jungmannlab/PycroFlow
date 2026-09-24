"""Parent-side coordinator that binds recording to the round lifecycle.

The controller lives in the orchestration process and owns the capture
subprocess. It attaches to an :class:`~PycroFlow.services.experiment_service.
ExperimentService` purely as observers -- a state observer for run start/stop
and a :class:`~PycroFlow.orchestration.signal_registry.SignalRegistry` observer
for round boundaries -- so ``ExperimentService`` needs no changes and monitoring
stays entirely optional.

Isolation on the hot path: the signal observer runs inline on the orchestration
threads, so it does **no** I/O -- it only drops a command onto a bounded,
drop-oldest in-process queue and returns. A daemon thread drains that queue and
writes the atomic control files the capture process reads. Even a stalled disk
or a dead capture child therefore cannot block or slow the exchange; the worst
case is a dropped command -> a logged gap.

Round windows are derived from the existing orchestration signals (no protocol
wire-format change): recording brackets each fluid *exchange* (flush) leg only,
not the long imaging leg. In the compiled protocol each flush leg runs as
``WAIT[img: done imaging round X]`` -> ``inject...`` -> ``signal: done flushing
Y`` -- the fluid handler waits for imaging to finish, then flushes. So:

* **With imaging:** a window opens on each ``img`` ``"done imaging round ..."``
  (the flush's WAIT is about to release) and closes on the next ``fluid``
  ``"done flushing ..."``. The imaging leg between a close and the next open is
  deliberately not recorded. Imaging and flush signals need not pair up
  one-to-one (dark rounds image without an exchange; the final flush may have no
  closing signal) -- the event-driven open/close handles that; a window left
  open at run end is closed then.
* **Fluid-only** (e.g. flushtest, no imaging signals): round 0 opens at run
  start and each ``"done flushing"`` closes the current window and opens the
  next, up to the flush count.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from PycroFlow.monitoring.config import (
    MonitoringConfig,
    load_monitoring_config,
)

_EMIT_QUEUE_MAX = 256


class MonitoringController:
    """Spawn/stop the capture process and bind clips to the round lifecycle.

    Parameters
    ----------
    service : ExperimentService
        The experiment service to observe. Not mutated.
    config : MonitoringConfig
        The rig's resolved camera config.
    mode : str
        ``'emulator'`` or ``'instrument'`` -- passed straight to the capture
        process (only the frame source differs).
    run_id : str or None
        Run identifier for filenames + registry rows; minted if ``None``.
    python_executable : str or None
        Interpreter used to spawn the capture module (defaults to the current
        one).
    """

    def __init__(
        self,
        service,
        config: MonitoringConfig,
        *,
        mode: str,
        run_id: Optional[str] = None,
        python_executable: Optional[str] = None,
        fail_mode: Optional[str] = None,
        fail_delay: float = 0.0,
    ):
        self._svc = service
        self._config = config
        self._mode = mode
        self.run_id = run_id
        self._python = python_executable or sys.executable
        # Fault injection for the isolation proof (emulator source only):
        # forwarded to the capture process so a slow/raising/unplugged camera
        # can be exercised end to end. Unused in production.
        self._fail_mode = fail_mode
        self._fail_delay = fail_delay

        self._proc: Optional[subprocess.Popen] = None
        self._control_dir: Optional[str] = None
        self._registry = None  # the SignalRegistry we subscribed to

        self._queue: "queue.Queue[dict]" = queue.Queue(maxsize=_EMIT_QUEUE_MAX)
        self._writer_thread: Optional[threading.Thread] = None
        self._writer_stop = threading.Event()
        self._seq = 0

        self._started = False
        self._spawned = False
        self._active = False  # a round window is currently open
        self._round = -1  # index of the current/last window (pre-first = -1)
        # Precomputed from the compiled protocol in _start(): for each "done
        # flushing" signal, whether an imaging wait follows it (so we hold
        # recording until the next "done imaging"), and whether the run's first
        # fluid action is a flush (so round 0 opens at run start).
        self._follows: list[bool] = []
        self._open_at_start = False
        self._df_seen = 0  # how many "done flushing" signals seen so far
        self._lock = threading.Lock()

    # -- attachment ------------------------------------------------------
    def attach(self) -> "MonitoringController":
        """Register the state observer and return self (for chaining)."""
        self._svc.add_state_observer(self._on_state)
        return self

    def detach(self) -> None:
        """Stop any active capture and deregister the state observer.

        Called when the frontend switches setups or shuts down, so a new
        controller can take over without the old one lingering.
        """
        self.stop()
        remove = getattr(self._svc, "remove_state_observer", None)
        if remove is not None:
            remove(self._on_state)

    # -- state machine (lifecycle) --------------------------------------
    def _on_state(self, old, new) -> None:
        # Lazy import to avoid any import cycle at module load.
        from PycroFlow.services.experiment_service import ExperimentState

        try:
            if new is ExperimentState.RUNNING and not self._started:
                self._start()
            elif (
                new in (ExperimentState.FINISHED, ExperimentState.ABORTED)
                and self._started
            ):
                self.stop()
        except Exception as exc:  # monitoring must never break the run
            logger.warning(
                "monitoring: controller state hook failed ({!r})", exc
            )

    def _start(self) -> None:
        with self._lock:
            self._started = True
            self._active = False
            self._round = -1
            self._df_seen = 0
            self._seq = 0
        proto = self._svc.protocol or {}
        fluid_entries = (proto.get("fluid") or {}).get(
            "protocol_entries"
        ) or []
        self._follows = _imaging_follows_flags(fluid_entries)
        self._open_at_start = _first_activity_is_flush(fluid_entries)
        if not self._follows:
            logger.info(
                "monitoring: no fluid exchanges in this run; monitoring inert"
            )
            return

        if self.run_id is None:
            self.run_id = "{}_{}".format(
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                uuid.uuid4().hex[:6],
            )
        self._resolved_output_dir = self._resolve_output_dir()
        try:
            self._spawn()
        except Exception as exc:  # a spawn failure must not stop the run
            logger.warning(
                "monitoring: could not start capture process ({!r}); "
                "continuing without monitoring",
                exc,
            )
            return

        # Subscribe to round-boundary signals, then open round 0.
        orch = getattr(self._svc, "orchestrator", None)
        tx = getattr(orch, "threadexchange", None)
        self._registry = tx.get("signal_registry") if tx is not None else None
        if self._registry is not None:
            self._registry.add_observer(self._on_signal)
        # Round 0 opens now only if the run flushes before it first waits for
        # imaging; otherwise it opens on the first "done imaging" (so we don't
        # record that leading imaging leg).
        if self._open_at_start:
            self._open()
        logger.info(
            "monitoring: recording {} exchange leg(s), run {}",
            len(self._follows),
            self.run_id,
        )

    def _resolve_output_dir(self) -> str:
        """Where clips land: the setup's ``output_dir`` if set, else
        ``<experiment save_dir>/fluidics_cam`` so clips travel with the run's
        outputs (the design load has already chdir'd there)."""
        if self._config.output_dir:
            return self._config.output_dir
        design = getattr(self._svc, "experiment_design", None) or {}
        base = os.path.abspath(design.get("save_dir") or ".")
        return os.path.join(base, "fluidics_cam")

    def _spawn(self) -> None:
        self._control_dir = tempfile.mkdtemp(prefix="pycroflow-cam-")
        cfg_path = os.path.join(self._control_dir, "cameras.json")
        cfg = self._config.to_dict()
        cfg["output_dir"] = self._resolved_output_dir
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        cmd = [
            self._python,
            "-m",
            "PycroFlow.monitoring.capture_service",
            "--config",
            cfg_path,
            "--control-dir",
            self._control_dir,
            "--run-id",
            str(self.run_id),
            "--instrument" if self._mode == "instrument" else "--emulator",
        ]
        if self._fail_mode:
            cmd += [
                "--fail-mode",
                self._fail_mode,
                "--fail-delay",
                str(self._fail_delay),
            ]
        self._proc = subprocess.Popen(cmd)
        self._writer_stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="cam-control-writer", daemon=True
        )
        self._writer_thread.start()
        self._spawned = True

    # -- round signal handling (runs on orchestration threads) -----------
    def _on_signal(self, target: str, value: str) -> None:
        # MUST be non-blocking: only touch the in-process queue. Close each
        # exchange on its "done flushing"; reopen immediately for a back-to-back
        # flush, else wait for the next "done imaging" (imaging leg not
        # recorded). This single rule handles fluid-only, dark rounds, and
        # initial-imager layouts alike.
        if target == "fluid" and "done flushing" in value:
            if self._active:
                self._close()
            follows = (
                self._follows[self._df_seen]
                if self._df_seen < len(self._follows)
                else True
            )
            self._df_seen += 1
            if not follows:
                self._open()  # next flush runs back-to-back
        elif target == "img" and "done imaging" in value:
            if not self._active:
                self._open()  # imaging done -> fluid resumes flushing

    # -- command emission (bounded, drop-oldest, never blocks) -----------
    def _open(self) -> None:
        self._round += 1
        self._active = True
        self._emit(
            {
                "cmd": "round_begin",
                "round_index": self._round,
                "unique_name": None,
                "protocol_step": self._current_fluid_step(),
                "t_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            }
        )

    def _current_fluid_step(self) -> Optional[int]:
        """The fluid handler's current run-sequence step, so the clip can be
        tied to the exact Run Sequence entry the exchange started on. ``None``
        if unavailable (never raises on the hot path)."""
        try:
            orch = getattr(self._svc, "orchestrator", None)
            handler = getattr(orch, "fluid_handler", None)
            getter = getattr(handler, "get_current_protocol_iter", None)
            return getter() if getter is not None else None
        except Exception:  # pragma: no cover - defensive
            return None

    def _close(self) -> None:
        self._active = False
        self._emit({"cmd": "round_end", "round_index": self._round})

    def _emit(self, cmd: dict) -> None:
        try:
            self._queue.put_nowait(cmd)
        except queue.Full:
            # Drop the oldest pending command to make room (best-effort): a
            # saturated queue means the disk/child is lagging -- degrade to a
            # gap rather than block the orchestration thread.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(cmd)
            except queue.Full:  # pragma: no cover - extreme back-pressure
                logger.warning("monitoring: dropped control command {!r}", cmd)

    def _writer_loop(self) -> None:
        # Drain until stopped *and* empty, so queued stop/end commands are
        # written before we tear down.
        while not self._writer_stop.is_set() or not self._queue.empty():
            try:
                cmd = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            self._write_cmd(cmd)

    def _write_cmd(self, cmd: dict) -> None:
        if not self._control_dir:
            return
        self._seq += 1
        name = "{:012d}.json".format(self._seq)
        path = os.path.join(self._control_dir, name)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(cmd, f)
            os.replace(tmp, path)  # atomic: child never sees a partial file
        except OSError as exc:  # best-effort
            logger.warning(
                "monitoring: could not write control cmd ({!r})", exc
            )

    # -- teardown --------------------------------------------------------
    def stop(self) -> None:
        """Close any open window, stop the capture child, clean up. Idempotent.

        Never raises -- teardown problems are logged, not propagated.
        """
        if not self._started:
            return
        try:
            if self._registry is not None:
                self._registry.remove_observer(self._on_signal)
            if self._active:
                self._close()
            if self._spawned:
                self._emit({"cmd": "stop"})
                self._drain_and_wait()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("monitoring: stop failed ({!r})", exc)
        finally:
            self._started = False
            self._spawned = False
            self._active = False
            self._registry = None

    def _drain_and_wait(self) -> None:
        # Let the writer flush the queued end/stop commands.
        deadline = time.monotonic() + 5.0
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        self._writer_stop.set()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=2.0)
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
        if self._control_dir:
            shutil.rmtree(self._control_dir, ignore_errors=True)
            self._control_dir = None


def _imaging_follows_flags(entries: list) -> list:
    """For each fluid ``"done flushing"`` signal, does an imaging wait follow it?

    Scans the compiled fluid entries once. For each ``done flushing`` signal, it
    looks at what the fluid handler does next: a ``wait for signal`` (imaging
    leg -> ``True``, hold recording until the next ``done imaging``) or another
    fluid action (a back-to-back flush -> ``False``, reopen immediately). A
    trailing signal with nothing after it counts as ``True`` (nothing to
    reopen). Returned list is aligned to the order of the ``done flushing``
    signals.
    """
    flags: list[bool] = []
    n = len(entries)
    for i, e in enumerate(entries):
        if not (
            isinstance(e, dict)
            and e.get("$type") == "signal"
            and "done flushing" in str(e.get("value", ""))
        ):
            continue
        follows = True
        for j in range(i + 1, n):
            nxt = entries[j]
            if not isinstance(nxt, dict):
                continue
            t = nxt.get("$type")
            if t == "wait for signal":
                follows = True
                break
            if t == "signal":
                continue
            follows = False  # any other entry is fluid activity
            break
        flags.append(follows)
    return flags


def _first_activity_is_flush(entries: list) -> bool:
    """True if the fluid handler flushes before it first waits for a signal.

    Determines whether round 0 should open at run start (flush-first layouts) or
    on the first ``done imaging`` (initial-imager layouts, where imaging runs
    before the first flush and shouldn't be recorded).
    """
    for e in entries:
        if not isinstance(e, dict):
            continue
        t = e.get("$type")
        if t == "wait for signal":
            return False
        if t == "signal":
            continue
        return True
    return False


def attach_monitoring(
    service, setup: dict, *, mode: Optional[str] = None, run_id=None
) -> Optional[MonitoringController]:
    """Build + attach a controller from a setup's ``monitoring:`` block.

    The single entry point frontends call after loading a setup + creating an
    experiment service. Returns ``None`` (subsystem inert) when the setup has
    no monitoring config -- so a no-camera rig runs unchanged.

    Parameters
    ----------
    service : ExperimentService
        The service to observe.
    setup : dict
        A setup config (:func:`PycroFlow.configs.load_setup`).
    mode : str or None
        Override the frame source. Default: the config's ``source``, else
        ``'emulator'`` when the setup is ``emulated``, else ``'instrument'``.
    run_id : str or None
        Optional run identifier; minted per run when omitted.
    """
    config = load_monitoring_config(setup)
    if config is None:
        return None
    if mode is None:
        if config.source:
            mode = config.source
        elif (setup or {}).get("emulated"):
            mode = "emulator"
        else:
            mode = "instrument"
    controller = MonitoringController(
        service, config, mode=mode, run_id=run_id
    )
    return controller.attach()
