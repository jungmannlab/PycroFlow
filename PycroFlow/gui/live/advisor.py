"""Advisor-findings adapter — the frontend's decoupled view of QC advice.

WP-GUI surfaces WP-ADVISOR's findings (the sidebar traffic-light + the Advisor
tab), but WP-ADVISOR lives in **picasso-workflow** and is built in parallel, so
its concrete ``qc_advisor`` findings type may not exist yet. We therefore render
against a **minimal local interface** defined here and feed it from an adapter,
so the GUI never hard-depends on picasso-workflow.

The assumed contract (kept intentionally small):

* a *finding* has ``severity`` (one of :data:`SEVERITIES`), a ``message``, an
  optional ``suggestion``, and an optional ``source`` (which check produced it);
* an *advisor* exposes ``findings_for(metrics: dict) -> list[Finding]`` — given
  the latest live metrics snapshot, return the current findings.

:class:`FindingsAdapter` coerces any object that *looks like* a finding (attrs or
dict keys ``severity`` / ``message`` / ``suggestion`` / ``source``) into
:class:`Finding`, so when the real ``picasso_workflow.qc_advisor`` type lands we
wire it in **without touching the panels**.

TODO(WP-ADVISOR): replace :class:`MockAdvisor` usage in the shell with the real
``picasso_workflow.qc_advisor`` advisor once it lands, via
:func:`FindingsAdapter.wrap` (which already coerces its finding objects). Keep the
adapter as the seam so the GUI stays decoupled if the real type differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, runtime_checkable

# Ordered worst-first so the sidebar light can take the max severity present.
SEVERITIES = ("error", "warning", "info", "ok")
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}

# Traffic-light colours (matches the V0.8 QC-at-a-glance palette family).
SEVERITY_COLOR = {
    "error": "#d9534f",  # red
    "warning": "#f0ad4e",  # amber
    "info": "#5bc0de",  # blue
    "ok": "#5cb85c",  # green
}

# WP-ADVISOR (picasso_workflow.qc_advisor) uses a different severity vocabulary
# ("bad"/"warn") and names its suggestion field "action". Normalise both here so
# its Finding drops in via FindingsAdapter WITHOUT a critical "bad" silently
# degrading to "info" (the Finding.__post_init__ fallback). Our own vocab and
# any already-normalised value pass through unchanged.
_SEVERITY_ALIASES = {
    "bad": "error",
    "warn": "warning",
    "error": "error",
    "warning": "warning",
    "info": "info",
    "ok": "ok",
}


@dataclass
class Finding:
    """One advisor finding, as the frontend renders it."""

    severity: str
    message: str
    suggestion: Optional[str] = None
    source: Optional[str] = None

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY_RANK:
            self.severity = "info"


@runtime_checkable
class Advisor(Protocol):
    """The minimal advisor surface the frontend consumes."""

    def findings_for(self, metrics: dict) -> List[Finding]:
        """Return the current findings for the latest metrics snapshot."""
        ...


def worst_severity(findings: List[Finding]) -> str:
    """The most severe severity across ``findings`` (``"ok"`` when empty)."""
    if not findings:
        return "ok"
    return min(
        findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 99)
    ).severity


class FindingsAdapter:
    """Coerce arbitrary finding-like objects into :class:`Finding`.

    Accepts dicts or duck-typed objects with ``severity`` / ``message`` /
    ``suggestion`` / ``source``. This is the boundary that lets the real
    ``picasso_workflow.qc_advisor`` findings type drop in unchanged.
    """

    @staticmethod
    def coerce(obj: Any) -> Finding:
        if isinstance(obj, Finding):
            return obj

        def _get(key: str) -> Any:
            if isinstance(obj, dict):
                return obj.get(key)
            return getattr(obj, key, None)

        sev = str(_get("severity") or "info").lower()
        return Finding(
            # Map WP-ADVISOR's bad/warn -> error/warning (own vocab passes through).
            severity=_SEVERITY_ALIASES.get(sev, sev),
            message=str(_get("message") or ""),
            # WP-ADVISOR names the suggestion field "action"; prefer our own
            # "suggestion" when present, else fall back to "action".
            suggestion=_get("suggestion") or _get("action"),
            source=_get("source"),
        )

    @classmethod
    def coerce_all(cls, objs: Any) -> List[Finding]:
        return [cls.coerce(o) for o in (objs or [])]

    @classmethod
    def wrap(cls, real_advisor: Any) -> "Advisor":
        """Wrap a real advisor so its findings are coerced on the way out.

        ``real_advisor`` need only expose ``findings_for(metrics) -> iterable``
        of finding-like objects. Use this to plug in ``picasso_workflow``'s
        advisor when it lands.
        """

        class _Wrapped:
            def findings_for(self, metrics: dict) -> List[Finding]:
                raw = real_advisor.findings_for(metrics)
                return cls.coerce_all(raw)

        return _Wrapped()


class MockAdvisor:
    """A deterministic stand-in used until the real advisor is wired.

    Emits findings from cheap threshold checks on the live metrics snapshot so
    the sidebar light + Advisor tab have something to render in tests and demos.
    The thresholds are placeholders — the real per-cohort bands come from
    WP-ADVISOR / the registry (A1/C19), not from here.
    """

    def __init__(
        self,
        *,
        nena_warn_nm: float = 8.0,
        nena_error_nm: float = 15.0,
        min_spots_per_frame: float = 0.05,
    ):
        self._nena_warn = nena_warn_nm
        self._nena_error = nena_error_nm
        self._min_spf = min_spots_per_frame

    def findings_for(self, metrics: dict) -> List[Finding]:
        out: List[Finding] = []
        nena = metrics.get("nena_nm")
        if nena is not None:
            if nena >= self._nena_error:
                out.append(
                    Finding(
                        "error",
                        "NeNA {:.1f} nm is very high".format(nena),
                        "Check focus / drift; consider aborting.",
                        "nena",
                    )
                )
            elif nena >= self._nena_warn:
                out.append(
                    Finding(
                        "warning",
                        "NeNA {:.1f} nm above target".format(nena),
                        "Increase photons or reduce background.",
                        "nena",
                    )
                )
        spf = metrics.get("spots_per_frame")
        if spf is not None and spf < self._min_spf:
            out.append(
                Finding(
                    "warning",
                    "Sparse: {:.3f} locs/frame".format(spf),
                    "Raise imager concentration or laser power.",
                    "density",
                )
            )
        return out
