"""Frontend-agnostic health checks for subsystems and external connectors.

The GUI's **Doctor** tab and the CLI's ``doctor`` command both consume this
service: it runs a list of named checks and returns structured
:class:`CheckResult` rows (status + human detail), so a single place answers
"which systems and connectors are actually working?" when a run misbehaves.

Each check is defensive — it never raises (an unexpected error becomes a
``FAIL`` row) — and verifies as much as is cheaply possible without side
effects: cached connection state, a serial ``get_status`` round-trip to each
fluid device, a Micro-Manager Core ping, the monet library plus whether its
config/protocol YAMLs loaded and the requested microscope was found, and an
HTTP reachability probe of the picasso-registry.

Checks that talk to the instruments (the serial pings, the MM Core ping) are
**skipped while a run is active** so they never contend with the orchestrator
for the bus; cached state and the network/library checks still run. Adding a
new connector is adding one small method that returns ``(status, detail)`` and
calling it from :meth:`DiagnosticsService.run_all`.

No secrets are ever placed in a result detail — the registry bearer token is
sent in the probe's ``Authorization`` header but never echoed back.
"""

from __future__ import annotations

import enum
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from loguru import logger

from PycroFlow.services.experiment_service import ExperimentState

#: Experiment states during which the orchestrator owns the instruments, so
#: the serial / MM-Core pings are skipped to avoid contending for the bus.
_ACTIVE_STATES = frozenset(
    {
        ExperimentState.ORCHESTRATING,
        ExperimentState.RUNNING,
        ExperimentState.PAUSED,
    }
)


class CheckStatus(enum.Enum):
    """Outcome of a single diagnostic check."""

    OK = "ok"
    SKIP = "skip"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    """One diagnostic row: a named check, its status, and a human detail."""

    name: str
    category: str
    status: CheckStatus
    detail: str = ""
    duration_s: Optional[float] = None


def summarize(results: List[CheckResult]) -> dict:
    """Count results per status (keys are the :class:`CheckStatus` members)."""
    counts = {status: 0 for status in CheckStatus}
    for result in results:
        counts[result.status] += 1
    return counts


class DiagnosticsService:
    """Run subsystem + connector health checks and return structured rows.

    Parameters
    ----------
    system_service : SystemService
        Source of connection state and the live subsystem objects.
    experiment_service : ExperimentService, optional
        Consulted only for the run state, so instrument-touching checks are
        skipped while a run owns the hardware.
    http_timeout : float, default: 4.0
        Per-request timeout for the registry reachability probe, in seconds.
    """

    DEFAULT_HTTP_TIMEOUT = 4.0

    def __init__(
        self,
        system_service,
        experiment_service=None,
        *,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    ):
        self._sys = system_service
        self._exp = experiment_service
        self._http_timeout = float(http_timeout)

    # -- public ---------------------------------------------------------------
    def run_all(self) -> List[CheckResult]:
        """Run every check and return the rows (grouped category by category)."""
        results: List[CheckResult] = []
        results.extend(self._environment_checks())
        results.extend(self._subsystem_checks())
        results.extend(self._connector_checks())
        return results

    def run_active(self) -> bool:
        """True when a run owns the instruments (pings are skipped)."""
        if self._exp is None:
            return False
        try:
            return self._exp.state in _ACTIVE_STATES
        except Exception:  # pragma: no cover - defensive
            return False

    # -- check runner ---------------------------------------------------------
    def _run(
        self,
        name: str,
        category: str,
        fn: Callable[[], Tuple[CheckStatus, str]],
    ) -> CheckResult:
        """Run one check function, turning any exception into a FAIL row."""
        start = time.perf_counter()
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a check must never raise
            logger.opt(exception=exc).debug("diagnostic {!r} raised", name)
            status, detail = CheckStatus.FAIL, "check raised: {!r}".format(exc)
        return CheckResult(
            name, category, status, detail, time.perf_counter() - start
        )

    # -- environment ----------------------------------------------------------
    def _environment_checks(self) -> List[CheckResult]:
        return [self._run("Setup", "Environment", self._check_setup)]

    def _check_setup(self) -> Tuple[CheckStatus, str]:
        name = self._sys.setup_name()
        if not name:
            return CheckStatus.WARN, "no setup loaded"
        emulated = " (emulated)" if self._sys.is_emulated() else ""
        return CheckStatus.OK, "{}{}".format(name, emulated)

    # -- subsystems -----------------------------------------------------------
    def _subsystem_checks(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        states = self._sys.connection_states()
        active = self.run_active()

        # Fluid: connection, then a serial ping of each device.
        results.append(self._connection_row("Fluid", states.get("fluid")))
        fs = self._sys.fluid_system
        if fs is not None:
            if active:
                results.append(
                    CheckResult(
                        "Fluid · devices",
                        "Subsystems",
                        CheckStatus.SKIP,
                        "skipped during a run (orchestrator owns the bus)",
                    )
                )
            else:
                results.extend(self._fluid_device_pings(fs))

        # Imaging: connection, then an MM Core ping.
        results.append(self._connection_row("Imaging", states.get("imaging")))
        if self._sys.imaging_system is not None:
            if active:
                results.append(
                    CheckResult(
                        "Imaging · MM Core",
                        "Subsystems",
                        CheckStatus.SKIP,
                        "skipped during a run",
                    )
                )
            else:
                results.append(
                    self._run(
                        "Imaging · MM Core",
                        "Subsystems",
                        self._check_mm_core,
                    )
                )

        # Illumination: connection, then the monet config / laser lines.
        results.append(
            self._connection_row("Illumination", states.get("illumination"))
        )
        if self._sys.illumination_system is not None:
            results.append(
                self._run(
                    "Illumination · lasers",
                    "Subsystems",
                    self._check_lasers,
                )
            )
        return results

    @staticmethod
    def _connection_row(label: str, connected) -> CheckResult:
        status = CheckStatus.OK if connected else CheckStatus.WARN
        detail = "connected" if connected else "not connected"
        return CheckResult(label, "Subsystems", status, detail)

    def _fluid_device_pings(self, fs) -> List[CheckResult]:
        """One serial ``get_status`` round-trip per pump / valve / multiplexer."""
        results: List[CheckResult] = []
        mux = getattr(fs, "multiplexer", None)
        for attr in ("pump_a", "pump_out"):
            pump = getattr(fs, attr, None)
            if pump is not None:
                results.append(
                    self._ping_device("Fluid · {}".format(attr), pump)
                )
        if mux is not None:
            results.append(self._ping_device("Fluid · ibidi multiplexer", mux))
        for addr, valve in (getattr(fs, "valve_a", {}) or {}).items():
            if valve is mux:
                continue  # already pinged as the multiplexer
            results.append(
                self._ping_device("Fluid · valve {}".format(addr), valve)
            )
        return results

    def _ping_device(self, label: str, device) -> CheckResult:
        def fn() -> Tuple[CheckStatus, str]:
            get_status = getattr(device, "get_status", None)
            if not callable(get_status):
                return CheckStatus.SKIP, "no get_status()"
            reply = get_status()
            if reply:
                return CheckStatus.OK, "responsive: {}".format(
                    str(reply).strip()
                )
            return CheckStatus.FAIL, "no response"

        return self._run(label, "Subsystems", fn)

    def _check_mm_core(self) -> Tuple[CheckStatus, str]:
        img = self._sys.imaging_system
        core = getattr(img, "core", None)
        if core is None:
            return CheckStatus.WARN, "no Micro-Manager Core handle"
        for meth in ("get_version_info", "getVersionInfo"):
            fn = getattr(core, meth, None)
            if callable(fn):
                return CheckStatus.OK, "responsive: {}".format(
                    str(fn()).strip()[:80]
                )
        return CheckStatus.OK, "Core handle present"

    def _check_lasers(self) -> Tuple[CheckStatus, str]:
        lasers = self._sys.laser_options()
        if lasers:
            return CheckStatus.OK, "{} laser line(s): {}".format(
                len(lasers), ", ".join(str(x) for x in lasers)
            )
        return CheckStatus.WARN, "no laser lines from the monet config"

    # -- connectors -----------------------------------------------------------
    def _connector_checks(self) -> List[CheckResult]:
        return [
            self._run(
                "Registry · client", "Connectors", self._check_registry_client
            ),
            self._run(
                "Registry · reachability",
                "Connectors",
                self._check_registry_reachable,
            ),
            self._run(
                "monet · library", "Connectors", self._check_monet_library
            ),
            self._run(
                "monet · configs", "Connectors", self._check_monet_configs
            ),
            self._run(
                "monet · microscope",
                "Connectors",
                self._check_monet_microscope,
            ),
        ]

    def _check_registry_client(self) -> Tuple[CheckStatus, str]:
        url = os.environ.get("PAINT_REGISTRY_URL")
        if not url:
            return (
                CheckStatus.SKIP,
                "not configured (PAINT_REGISTRY_URL unset)",
            )
        try:
            import picasso_registry.buffered_client  # noqa: F401
        except Exception:
            return (
                CheckStatus.WARN,
                "configured but picasso_registry is not installed",
            )
        token = "set" if os.environ.get("PAINT_REGISTRY_TOKEN") else "unset"
        return CheckStatus.OK, "client available; token {}".format(token)

    def _check_registry_reachable(self) -> Tuple[CheckStatus, str]:
        url = os.environ.get("PAINT_REGISTRY_URL")
        if not url:
            return (
                CheckStatus.SKIP,
                "not configured (PAINT_REGISTRY_URL unset)",
            )
        return self._http_probe(url, os.environ.get("PAINT_REGISTRY_TOKEN"))

    def _http_probe(
        self, url: str, token: Optional[str]
    ) -> Tuple[CheckStatus, str]:
        """GET ``url`` with a short timeout; any HTTP reply means reachable.

        The bearer ``token`` is sent in the ``Authorization`` header but never
        echoed into the returned detail.
        """
        req = urllib.request.Request(url, method="GET")
        if token:
            req.add_header("Authorization", "Bearer {}".format(token))
        try:
            with urllib.request.urlopen(
                req, timeout=self._http_timeout
            ) as resp:
                return CheckStatus.OK, "reachable ({} HTTP {})".format(
                    url, resp.getcode()
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return (
                    CheckStatus.WARN,
                    "reachable but auth rejected (HTTP {} — check "
                    "PAINT_REGISTRY_TOKEN)".format(exc.code),
                )
            return CheckStatus.WARN, "reachable ({} HTTP {})".format(
                url, exc.code
            )
        except urllib.error.URLError as exc:
            return CheckStatus.FAIL, "unreachable: {}".format(exc.reason)
        except (TimeoutError, socket.timeout):
            return CheckStatus.FAIL, "timed out after {:.0f}s".format(
                self._http_timeout
            )

    @staticmethod
    def _import_monet():
        """Return the ``monet`` module, or None if it cannot be imported."""
        try:
            import monet

            return monet
        except Exception:
            return None

    def _monet_setup_name(self) -> Optional[str]:
        """The monet ``CONFIGS`` key the loaded setup illuminates with.

        This is the *microscope* requested (``illumination.config``), which may
        differ from the setup's own name. None when no setup is loaded.
        """
        getter = getattr(self._sys, "get_monet_setup", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # pragma: no cover - defensive
            return None

    def _check_monet_library(self) -> Tuple[CheckStatus, str]:
        monet = self._import_monet()
        if monet is None:
            return CheckStatus.WARN, "monet is not installed"
        # In tests / without the real package monet is a MagicMock, whose
        # CONFIGS is not a real dict — flag that rather than reporting healthy.
        if not isinstance(getattr(monet, "CONFIGS", None), dict):
            return (
                CheckStatus.WARN,
                "monet imported but appears mocked (CONFIGS is not a dict)",
            )
        return CheckStatus.OK, "monet library importable (local)"

    def _check_monet_configs(self) -> Tuple[CheckStatus, str]:
        """Whether monet's config + protocol YAMLs have been loaded."""
        monet = self._import_monet()
        if monet is None:
            return CheckStatus.SKIP, "monet not installed"
        configs = getattr(monet, "CONFIGS", None)
        protocols = getattr(monet, "PROTOCOLS", None)
        if not isinstance(configs, dict) or not isinstance(protocols, dict):
            return (
                CheckStatus.WARN,
                "CONFIGS/PROTOCOLS not loaded (monet uninitialised or mocked)",
            )
        if not configs:
            return CheckStatus.WARN, "no monet configs loaded (0 microscopes)"
        detail = "{} config(s), {} protocol(s)".format(
            len(configs), len(protocols)
        )
        if not protocols:
            return (
                CheckStatus.WARN,
                "configs loaded but no protocols — " + detail,
            )
        return CheckStatus.OK, detail

    def _check_monet_microscope(self) -> Tuple[CheckStatus, str]:
        """Whether the microscope the setup requests exists in monet."""
        name = self._monet_setup_name()
        if not name:
            return (
                CheckStatus.SKIP,
                "no microscope requested (no setup / illumination config)",
            )
        monet = self._import_monet()
        if monet is None:
            return CheckStatus.SKIP, "monet not installed"
        configs = getattr(monet, "CONFIGS", None)
        protocols = getattr(monet, "PROTOCOLS", None)
        if not isinstance(configs, dict):
            return (
                CheckStatus.WARN,
                "cannot verify {!r}: monet.CONFIGS unavailable".format(name),
            )
        in_configs = name in configs
        in_protocols = isinstance(protocols, dict) and name in protocols
        if in_configs and in_protocols:
            return CheckStatus.OK, "{!r} found (config + protocol)".format(
                name
            )
        if in_configs:
            return (
                CheckStatus.WARN,
                "{!r} has a config but no protocol".format(name),
            )
        have = ", ".join(sorted(configs)) if configs else "none"
        return (
            CheckStatus.FAIL,
            "{!r} not found in monet configs (have: {})".format(name, have),
        )


__all__ = [
    "CheckStatus",
    "CheckResult",
    "DiagnosticsService",
    "summarize",
]
