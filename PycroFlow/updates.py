"""Check whether a newer PycroFlow release is available on GitHub.

Mirrors monet's ``updates`` module: the GUI's Help ▸ "Check for Updates…"
queries the public GitHub Releases API for the latest ``vX.Y.Z`` tag and
compares it to the installed :data:`PycroFlow.__version__` (both derived from
git tags via setuptools-scm). Never raises — a network/offline error is
reported in the returned dict so the GUI can show a friendly message — and a
non-release source tree (version ``"0.0.0"``/``"unknown"``) never nags.
"""

from __future__ import annotations

from loguru import logger

GITHUB_REPO = "jungmannlab/PycroFlow"
RELEASES_URL = "https://github.com/{}/releases".format(GITHUB_REPO)
LATEST_RELEASE_API = "https://api.github.com/repos/{}/releases/latest".format(
    GITHUB_REPO
)

#: Versions that mean "not a tagged release" — never report an update for them.
_NON_RELEASE = {"", "unknown", "0.0.0"}


def _normalize(tag) -> str:
    """Strip a leading ``v`` from a release tag (``v1.2.3`` -> ``1.2.3``)."""
    tag = (tag or "").strip()
    return tag[1:] if tag[:1].lower() == "v" else tag


def _is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` is strictly newer than ``current``.

    Prefers :mod:`packaging.version` (PEP 440 ordering); falls back to a
    numeric-tuple compare. An unparseable or non-release ``current`` (an
    untagged / editable source tree) is treated as "not newer", so a dev build
    is never nagged about.
    """
    if not latest or current in _NON_RELEASE:
        return False
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            return False
    except Exception:  # pragma: no cover - packaging is ~always present

        def _tuple(v):
            return tuple(int(p) for p in v.split(".") if p.isdigit())

        try:
            return _tuple(latest) > _tuple(current)
        except Exception:
            return False


def latest_release(timeout: float = 10) -> str:
    """Return the latest release's normalised tag (e.g. ``"0.3.1"``).

    Raises on a network/HTTP/parse error — callers wanting a verdict dict use
    :func:`check_for_update`.
    """
    import requests

    resp = requests.get(
        LATEST_RELEASE_API,
        timeout=timeout,
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    return _normalize(resp.json().get("tag_name"))


def check_for_update(current: str | None = None, timeout: float = 10) -> dict:
    """Compare the installed version against the latest GitHub release.

    Never raises: an offline/HTTP error is reported in the returned dict.

    Returns
    -------
    dict
        ``current`` (installed), ``latest`` (or ``None`` on error),
        ``update_available`` (bool), ``url`` (the Releases page), and ``error``
        (a human-readable string, or ``None``).
    """
    if current is None:
        import PycroFlow

        current = PycroFlow.__version__
    result = {
        "current": current,
        "latest": None,
        "update_available": False,
        "url": RELEASES_URL,
        "error": None,
    }
    try:
        latest = latest_release(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        logger.debug("update check failed", exc_info=True)
        result["error"] = str(exc)
        return result
    result["latest"] = latest
    result["update_available"] = _is_newer(latest, current)
    return result
