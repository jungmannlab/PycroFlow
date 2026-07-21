"""High-level experiment lifecycle service.

Wraps :class:`PycroFlow.orchestration.ProtocolOrchestrator` so frontends
(CLI today, Qt GUI in Stage 5) talk to a small use-case API instead of
poking orchestrator internals. The observer hook lets the GUI subscribe
to state transitions via plain callbacks; Stage 5's
``gui/qt_bridge.py`` translates those into Qt signals on the GUI thread.

The service is intentionally thin — it does not start hardware or wire
up systems (that's the caller's job, typically through
:class:`SystemService`). It owns the orchestrator instance and the
protocol that was loaded.
"""

from __future__ import annotations

import enum
import os
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

import yaml
from loguru import logger

from PycroFlow.orchestration import ProtocolOrchestrator


class ExperimentState(enum.Enum):
    """Lifecycle states observable by frontends."""

    IDLE = "idle"
    LOADED = "loaded"  # protocol parsed, orchestrator not started
    # handler threads running, protocol not started
    ORCHESTRATING = "orchestrating"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    ABORTED = "aborted"


StateObserver = Callable[[ExperimentState, ExperimentState], None]
LogObserver = Callable[[str], None]


class ExperimentService:
    """Frontend-facing API for running protocols.

    Usage::

        svc = ExperimentService(
            imaging_system=imgsys, fluid_system=fluidsys,
            illumination_system=illusys,
        )
        svc.add_state_observer(lambda old, new: print(f'{old} -> {new}'))
        svc.load_protocol_from_yaml(path)
        svc.start()
        svc.pause()
        svc.resume()
        svc.abort()
    """

    def __init__(
        self,
        imaging_system=None,
        fluid_system=None,
        illumination_system=None,
    ):
        self._imaging_system = imaging_system
        self._fluid_system = fluid_system
        self._illumination_system = illumination_system
        self._experiment_design: Optional[Dict] = None
        self._protocol: Optional[Dict] = None
        self._orchestrator: Optional[ProtocolOrchestrator] = None
        # Snapshot of the systems the current orchestrator was built with, so
        # start() can tell whether hardware was connected after load/translate.
        self._orchestrator_systems = None
        self._state = ExperimentState.IDLE
        self._lock = threading.Lock()
        self._state_observers: List[StateObserver] = []
        self._log_observers: List[LogObserver] = []

    # --- Subsystem wiring ---------------------------------------------

    def attach_systems(
        self,
        fluid_system=None,
        imaging_system=None,
        illumination_system=None,
    ) -> None:
        """Set the subsystem objects used to build the orchestrator.

        Frontends call this after connecting hardware (e.g. the GUI System
        tab) so a subsequent :meth:`load_protocol` / :meth:`start` includes
        them. Refused while an orchestrator is active — abort first, since
        the running orchestrator already captured the previous systems.

        Parameters
        ----------
        fluid_system, imaging_system, illumination_system : object or None
            The subsystem instances (or None when not connected).
        """
        if self._state in (
            ExperimentState.ORCHESTRATING,
            ExperimentState.RUNNING,
            ExperimentState.PAUSED,
        ):
            raise RuntimeError(
                "cannot change systems while an experiment is active "
                "(state={!r}); abort first".format(self._state)
            )
        self._fluid_system = fluid_system
        self._imaging_system = imaging_system
        self._illumination_system = illumination_system

    # --- Experiment design (high-level) -------------------------------

    def load_experiment_design(self, source) -> Dict:
        """Load + validate a high-level experiment design.

        Parameters
        ----------
        source : dict or str
            A design dict (e.g. from the GUI editor) or a path to a design
            YAML file.

        Returns
        -------
        dict
            The validated design (with on-disk/aliased keys), stored for
            :meth:`translate`.

        Notes
        -----
        When loaded from a file, the process working directory is changed to
        the design's containing folder, so the Run Sequence YAML and any
        acquisition output created during the run land next to the design.
        """
        from PycroFlow.schemas import validate_experiment_design

        if isinstance(source, str):
            with open(source) as f:
                data = yaml.safe_load(f)
        else:
            data = source
        model = validate_experiment_design(data)
        self._experiment_design = model.model_dump(by_alias=True)
        if isinstance(source, str):
            folder = os.path.dirname(os.path.abspath(source))
            os.chdir(folder)
            logger.info("Working directory changed to {}", folder)
        self._redirect_logs_to_acquisition_folder()
        return self._experiment_design

    def _redirect_logs_to_acquisition_folder(self):
        """Move the log files next to this experiment's acquisition output.

        The logs are the record of what the run actually did (including the
        per-step timings), so they belong with the data rather than in
        whichever directory the app was started from.
        """
        import PycroFlow

        design = self._experiment_design or {}
        save_dir = os.path.abspath(design.get('save_dir') or '.')
        try:
            PycroFlow.redirect_logging(save_dir)
        except Exception as exc:   # never fail a load over logging
            logger.warning("could not redirect logs: {!r}", exc)

    def save_run_record(self):
        """Write the design and Run Sequence about to run into ``save_dir``.

        A run's inputs belong with its output: the experiment design as
        loaded (including any GUI edits) and the compiled Run Sequence that
        was actually executed. Filenames carry a timestamp, so re-running
        into the same acquisition folder records each run rather than
        overwriting the previous one's evidence.

        Returns
        -------
        list of str
            The files written (empty when there is nothing to write, or on
            failure — a save-record problem must never stop a run).
        """
        design = self._experiment_design
        if not design:
            # No design means no acquisition folder — a bare protocol loaded
            # from a file already exists on disk, and writing the record
            # relative to the cwd would scatter files wherever the app runs.
            logger.debug(
                "no experiment design loaded; not saving a run record")
            return []
        stamp = datetime.now().strftime('%y%m%d-%H%M%S')
        base = design.get('base_name') or 'experiment'
        save_dir = os.path.abspath(design.get('save_dir') or '.')
        artifacts = [('design', design), ('run_sequence', self._protocol)]
        written = []
        try:
            os.makedirs(save_dir, exist_ok=True)
            for kind, data in artifacts:
                if not data:
                    continue
                path = os.path.join(
                    save_dir, '{}_{}_{}.yaml'.format(base, stamp, kind))
                with open(path, 'w') as f:
                    yaml.dump(data, f, default_flow_style=False,
                              sort_keys=False)
                written.append(path)
        except Exception as exc:   # never block a run over bookkeeping
            logger.warning("could not save the run record: {!r}", exc)
            return written
        if written:
            logger.info("Saved run record: {}", ', '.join(written))
        return written

    @property
    def experiment_design(self) -> Optional[Dict]:
        return self._experiment_design

    def translate(self) -> Dict:
        """Compile the loaded design into a Run Sequence and load it.

        Returns
        -------
        dict
            The compiled protocol (also loaded via :meth:`load_protocol`).
        """
        if self._experiment_design is None:
            raise RuntimeError("no experiment design loaded")
        from PycroFlow.protocols import ProtocolBuilder

        protocol = ProtocolBuilder().build_protocol(self._experiment_design)
        self.load_protocol(protocol)
        return protocol

    # --- Protocol loading ---------------------------------------------

    def load_protocol(self, protocol: Dict) -> None:
        """Accept a parsed protocol dict and prepare an orchestrator."""
        if self._state in (
            ExperimentState.ORCHESTRATING,
            ExperimentState.RUNNING,
            ExperimentState.PAUSED,
        ):
            raise RuntimeError(
                "cannot load a protocol while one is active "
                "(state={!r}); abort first".format(self._state)
            )
        self._protocol = protocol
        self._build_orchestrator()
        self._set_state(ExperimentState.LOADED)

    def _build_orchestrator(self) -> None:
        """(Re)build the orchestrator from the current protocol + systems.

        Called on load and again on :meth:`start`, so subsystems connected
        *after* a protocol/design was loaded are still picked up (otherwise
        the handlers see ``system=None`` and finish immediately).
        """
        # Only wire a subsystem's hardware when the protocol actually contains
        # its steps. A deselected subsystem (``enabled: false`` in the design)
        # has no protocol key; attaching its system anyway would give that
        # handler an empty/missing entry list and crash it mid-run.
        proto = self._protocol or {}
        self._orchestrator = ProtocolOrchestrator(
            self._protocol,
            imaging_system=(self._imaging_system if "img" in proto else None),
            fluid_system=(self._fluid_system if "fluid" in proto else None),
            illumination_system=(
                self._illumination_system if "illu" in proto else None
            ),
        )
        self._orchestrator_systems = self._current_systems()

    def _current_systems(self):
        return (
            self._fluid_system,
            self._imaging_system,
            self._illumination_system,
        )

    def load_protocol_from_yaml(self, path: str) -> None:
        """Load a protocol from a YAML file (the format produced by
        :meth:`PycroFlow.protocols.ProtocolBuilder.create_protocol`)."""
        with open(path) as f:
            self.load_protocol(yaml.safe_load(f))

    # --- Lifecycle ----------------------------------------------------

    def start(self, system_steps: Optional[Dict] = None) -> None:
        self._require_orchestrator()
        if self._state in (
            ExperimentState.LOADED,
            ExperimentState.FINISHED,
            ExperimentState.ABORTED,
        ):
            # A finished/aborted run leaves dead handler threads and set
            # finished/abort flags, so always rebuild for a fresh run from
            # those states. From LOADED, rebuild only if hardware was
            # connected after load/translate (otherwise the handlers see
            # system=None and the protocol silently finishes immediately);
            # skipping it when systems are unchanged preserves an
            # externally-set orchestrator.
            if (
                self._state is not ExperimentState.LOADED
                or self._current_systems() != self._orchestrator_systems
            ):
                self._build_orchestrator()
            if not any(self._current_systems()):
                logger.warning(
                    "Starting with no subsystems connected — the protocol "
                    "will finish immediately. Connect hardware (or the "
                    "Emulator setup) in the System tab first."
                )
            self._orchestrator.start_orchestration()
            self._set_state(ExperimentState.ORCHESTRATING)
        if self._state in (
            ExperimentState.ORCHESTRATING,
            ExperimentState.PAUSED,
        ):
            self.save_run_record()
            self._orchestrator.start_protocol(system_steps or {})
            self._set_state(ExperimentState.RUNNING)

    def pause(self) -> None:
        self._require_orchestrator()
        self._orchestrator.pause_protocol()
        self._set_state(ExperimentState.PAUSED)

    def resume(self) -> None:
        self._require_orchestrator()
        self._orchestrator.resume_protocol()
        self._set_state(ExperimentState.RUNNING)

    def abort(self) -> None:
        if self._orchestrator is None:
            return
        self._orchestrator.abort_protocol()
        self._set_state(ExperimentState.ABORTED)

    def clear_design(self) -> None:
        """Forget the loaded experiment design.

        Independent of the run sequence: a compiled protocol stays loaded.
        """
        self._experiment_design = None

    def clear_protocol(self) -> None:
        """Unload the current run sequence and reset to IDLE.

        Refused while an experiment is active (abort it first); the running
        orchestrator owns the hardware.
        """
        if self._state in (
            ExperimentState.ORCHESTRATING,
            ExperimentState.RUNNING,
            ExperimentState.PAUSED,
        ):
            raise RuntimeError(
                "cannot clear the run sequence while an experiment is active "
                "(state={!r}); abort first".format(self._state)
            )
        self._protocol = None
        self._orchestrator = None
        self._orchestrator_systems = None
        self._set_state(ExperimentState.IDLE)

    def end(self) -> None:
        """Gracefully end the orchestrator (handler threads stop after
        completing in-flight work)."""
        if self._orchestrator is None:
            return
        self._orchestrator.end_orchestration()
        self._set_state(ExperimentState.FINISHED)

    # --- Status / introspection ---------------------------------------

    @property
    def state(self) -> ExperimentState:
        return self._state

    @property
    def orchestrator(self) -> Optional[ProtocolOrchestrator]:
        """Escape hatch for code that needs the raw orchestrator (tests,
        legacy paths). Avoid in new frontend code."""
        return self._orchestrator

    @property
    def protocol(self) -> Optional[Dict]:
        return self._protocol

    def progress(self) -> Dict:
        """Per-subsystem execution progress for the live GUI status.

        Returns
        -------
        dict
            Maps ``'fluid'`` / ``'img'`` / ``'illu'`` to ``(current, total)``
            step indices. ``current`` is the step the handler is on (== total
            when that subsystem has no system and finished immediately).
            Empty dict when nothing is loaded.
        """
        if self._orchestrator is None or self._protocol is None:
            return {}
        handlers = {
            "fluid": self._orchestrator.fluid_handler,
            "img": self._orchestrator.imaging_handler,
            "illu": self._orchestrator.illumination_handler,
        }
        out = {}
        for key, handler in handlers.items():
            sub = self._protocol.get(key, {})
            entries = (
                sub.get("protocol_entries", [])
                if isinstance(sub, dict)
                else []
            )
            total = len(entries)
            if handler.system is None:
                cur = total
            else:
                cur = handler.get_current_protocol_iter()
            out[key] = (cur, total)
        return out

    def step_progress(self) -> Dict:
        """Per-subsystem progress *within* the current step.

        Returns
        -------
        dict
            Maps ``'fluid'`` / ``'img'`` / ``'illu'`` to ``(current, total,
            label)`` for steps that have meaningful sub-progress (imaging
            frames, fluid incubation wait), or ``None`` otherwise. Empty dict
            when nothing is loaded.
        """
        if self._orchestrator is None:
            return {}
        handlers = {
            "fluid": self._orchestrator.fluid_handler,
            "img": self._orchestrator.imaging_handler,
            "illu": self._orchestrator.illumination_handler,
        }
        out = {}
        for key, handler in handlers.items():
            getter = getattr(handler, "get_step_progress", None)
            out[key] = getter() if getter is not None else None
        return out

    def is_finished(self) -> bool:
        if self._orchestrator is None:
            return False
        return self._orchestrator.poll_protocol_finished()

    # --- Observers ----------------------------------------------------

    def add_state_observer(self, fn: StateObserver) -> None:
        """Register a callback fired on every state transition.

        ``fn`` is called with (old_state, new_state). Used by the Qt GUI's
        ``qt_bridge`` to translate transitions into Qt signals on the GUI
        thread.
        """
        self._state_observers.append(fn)

    def add_log_observer(self, fn: LogObserver) -> None:
        """Register a callback fired for log lines the service decides to
        surface to frontends. Currently emitted on every state change as a
        formatted line; future stages may forward orchestrator log entries.
        """
        self._log_observers.append(fn)

    # --- Internals ----------------------------------------------------

    def _require_orchestrator(self) -> None:
        if self._orchestrator is None:
            raise RuntimeError("no protocol loaded; call load_protocol first")

    def _set_state(self, new_state: ExperimentState) -> None:
        with self._lock:
            old, self._state = self._state, new_state
        if old is new_state:
            return
        logger.debug(
            "ExperimentService: {} -> {}".format(old.value, new_state.value)
        )
        for fn in list(self._state_observers):
            try:
                fn(old, new_state)
            except Exception as exc:
                logger.warning("state observer raised: {!r}".format(exc))
        msg = "experiment {} -> {}".format(old.value, new_state.value)
        for fn in list(self._log_observers):
            try:
                fn(msg)
            except Exception as exc:
                logger.warning("log observer raised: {!r}".format(exc))
