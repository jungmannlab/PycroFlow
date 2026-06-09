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
import threading
from typing import Callable, Dict, List, Optional

import yaml
from loguru import logger

from PycroFlow.orchestration import ProtocolOrchestrator


class ExperimentState(enum.Enum):
    """Lifecycle states observable by frontends."""
    IDLE = "idle"
    LOADED = "loaded"           # protocol parsed, orchestrator not started
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
        """
        from PycroFlow.schemas import validate_experiment_design

        if isinstance(source, str):
            with open(source) as f:
                data = yaml.safe_load(f)
        else:
            data = source
        model = validate_experiment_design(data)
        self._experiment_design = model.model_dump(by_alias=True)
        return self._experiment_design

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
        self._orchestrator = ProtocolOrchestrator(
            protocol,
            imaging_system=self._imaging_system,
            fluid_system=self._fluid_system,
            illumination_system=self._illumination_system,
        )
        self._set_state(ExperimentState.LOADED)

    def load_protocol_from_yaml(self, path: str) -> None:
        """Load a protocol from a YAML file (the format produced by
        :meth:`PycroFlow.protocols.ProtocolBuilder.create_protocol`)."""
        with open(path) as f:
            self.load_protocol(yaml.safe_load(f))

    # --- Lifecycle ----------------------------------------------------

    def start(self, system_steps: Optional[Dict] = None) -> None:
        self._require_orchestrator()
        if self._state == ExperimentState.LOADED:
            self._orchestrator.start_orchestration()
            self._set_state(ExperimentState.ORCHESTRATING)
        if self._state in (
            ExperimentState.ORCHESTRATING,
            ExperimentState.PAUSED,
        ):
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
            "ExperimentService: {} -> {}".format(
                old.value, new_state.value))
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
