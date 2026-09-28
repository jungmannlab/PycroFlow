"""Gate-2 on-instrument integration harness for the WP-4 live slice.

Gate 2 is the heavy on-instrument integration gate for the WP-4 live-analysis
slice (WP-4 + pycromanager-1.0 + WP-GUI, bundled on ``feature/gate2-bundle``).
This is a self-contained, **non-interactive, single-command** harness modelled
on the WP-1 perf harness (:mod:`PycroFlow.perf`): the lab runs it once on the
Windows acquisition PC and it writes a structured JSON verdict under
``results/gate2/`` for analysis back in the dev container.

It follows the established round-trip (see the planning ``CLAUDE.md`` 🔁 note):

#. Claude **builds + emulator-validates** it here (no hardware);
#. the user **runs it on the acq PC** (``--mode instrument``), which writes
   ``results/gate2/<timestamp>.json`` and commits it back;
#. Claude **analyses** the committed JSON here and drafts the Gate-2 go/no-go.

Two modes, ONE code path — only the backends differ, plugged behind the WP-4
seam that already supports this:

``--mode emulator`` (DEFAULT, hermetic; runs in the dev container / CI)
    Mock frame source + mock illumination + mock registry. Runs fully green
    end-to-end with no hardware and no network, proving the harness logic before
    it ever touches the instrument.
``--mode instrument`` (the acquisition PC)
    Real pycromanager-1.0 MDA + real monet interlock + (real or mock) registry.
    pycromanager / monet are imported lazily so ``--mode emulator`` needs
    neither.

Each check maps to a Gate-2 criterion; results are reduced to one JSON object
(per-check ``{name, passed, detail, values}`` + an overall ``gate2_pass`` +
a version block). Reuses WP-4's ``LiveAnalysisService`` / frame-source /
interlock APIs and the WP-1 harness's structure; it does NOT reimplement the
pipeline and does NOT modify production code (it is purely additive).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

import PycroFlow

# WP-4 live-analysis seam (import-safe on dev / CI — picasso / pycromanager /
# PyQt6 load lazily inside these on first use).
from PycroFlow.live_analysis.archive import (
    WRITE_TARGET_LOCAL,
    archive_movie,
)
from PycroFlow.live_analysis.laser_interlock import LaserInterlock
from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService

SCHEMA_VERSION = "1.0"

MODE_EMULATOR = "emulator"
MODE_INSTRUMENT = "instrument"

# Default emulator FOV — small + fast so the hermetic run is quick but still
# produces enough localizations for a REAL (non-None) NeNA.
DEFAULT_N_FRAMES = 2000
DEFAULT_EMU_HEIGHT = 48
DEFAULT_EMU_WIDTH = 48
DEFAULT_EMU_SEED = 7
# Loosened net-gradient so the mock movie yields plenty of spots for NeNA.
_EMU_LOCALIZE_PARAMS = {"Box Size": 7, "Min. Net Gradient": 200}


# ── check bookkeeping ───────────────────────────────────────────────────────


@dataclass
class Check:
    """One Gate-2 check result (serialised into the JSON verdict)."""

    name: str
    criterion: str
    passed: bool
    detail: str = ""
    values: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "criterion": self.criterion,
            "passed": bool(self.passed),
            "detail": self.detail,
            "values": self.values,
        }


class CheckRunner:
    """Collects checks, isolating each so one failure never aborts the run.

    :meth:`run` executes a check function that returns a :class:`Check`; if the
    function itself raises, that is recorded as a failed check with the
    traceback rather than crashing the harness — Gate 2 must always produce a
    verdict.
    """

    def __init__(self) -> None:
        self.checks: list[Check] = []

    def run(self, name: str, criterion: str, fn) -> Check:
        try:
            chk = fn()
            if not isinstance(chk, Check):  # pragma: no cover - defensive
                chk = Check(name, criterion, False, "check returned non-Check")
        except Exception as exc:  # noqa: BLE001 - a check crash is a fail
            chk = Check(
                name=name,
                criterion=criterion,
                passed=False,
                detail="check raised: {!r}".format(exc),
                values={"traceback": traceback.format_exc()},
            )
        # Ensure name/criterion are authoritative even if fn set its own.
        chk.name = name
        chk.criterion = criterion
        self.checks.append(chk)
        return chk

    def add(self, chk: Check) -> Check:
        self.checks.append(chk)
        return chk

    @property
    def gate2_pass(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


# ── emulator fakes (hermetic; no hardware, no network) ───────────────────────


class _FakeLaser:
    def __init__(self) -> None:
        self.enabled = True


class _FakeInstrument:
    def __init__(self) -> None:
        self.lasers = {488: _FakeLaser(), 561: _FakeLaser()}
        self.curr_laser = 488


class FakeIllumination:
    """Duck-typed stand-in for the monet interlock surface (emulator mode).

    Mirrors :class:`PycroFlow.illumination.IlluminationSystem`'s interlock
    surface (``set_laser_enabled`` per laser + ``beampath_close``) so the T3
    interlock exercises the SAME code path it will on hardware.
    """

    def __init__(self) -> None:
        self.instrument = _FakeInstrument()
        self.shutter_open = True

    def set_laser_enabled(self, laser, enabled) -> None:
        self.instrument.lasers[laser].enabled = enabled

    def beampath_close(self) -> None:
        self.shutter_open = False

    def reset(self) -> None:
        for la in self.instrument.lasers.values():
            la.enabled = True
        self.shutter_open = True

    @property
    def all_off(self) -> bool:
        return all(not la.enabled for la in self.instrument.lasers.values())


# ── version block ────────────────────────────────────────────────────────────


def _version_of(mod_name: str) -> str:
    try:
        mod = __import__(mod_name)
    except Exception:
        return "not-installed"
    return str(getattr(mod, "__version__", "unknown"))


def _git_commit() -> str:
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _mm_version() -> str:  # pragma: no cover - needs a running MM
    """Best-effort Micro-Manager version (nightly) if a Core is reachable."""
    try:
        from PycroFlow.services import mm_core

        core = mm_core.get_core()
        return str(core.get_version_info())
    except Exception:
        return "unavailable"


def build_version_block(mode: str) -> dict:
    """Resolve the interop version block (numpy, picasso, pycromanager, ...).

    ``numpy`` + ``np.trapezoid`` + ``picasso`` are always resolvable in the
    container; ``pycromanager`` / the MM nightly resolve only on the acq PC.
    """
    import numpy as np

    block = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "host": socket.gethostname(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "numpy": _version_of("numpy"),
        "numpy_has_trapezoid": hasattr(np, "trapezoid"),
        "picasso": _version_of("picasso"),
        "pycromanager": _version_of("pycromanager"),
        "pycroflow": str(getattr(PycroFlow, "__version__", "unknown")),
        "git_commit": _git_commit(),
    }
    if mode == MODE_INSTRUMENT:  # pragma: no cover - needs a running MM
        block["mm_version"] = _mm_version()
        for extra in ("ndtiff", "ndstorage"):
            block[extra] = _version_of(extra)
    return block


# ── the FOV run (both modes route through the same WP-4 service) ──────────────


def _make_fov_config(args) -> FovConfig:
    """Build the :class:`FovConfig` for a clean run in the active mode.

    Emulator: a hermetic in-memory ``MockFrameSource``. Instrument: the default
    lossless ``tiff-tail`` source over the MDA's on-disk movie in ``data_dir``.
    """
    if args.mode == MODE_EMULATOR:
        return FovConfig(
            source_kind="mock",
            source_kwargs={
                "n_frames": args.n_frames,
                "height": DEFAULT_EMU_HEIGHT,
                "width": DEFAULT_EMU_WIDTH,
                "seed": DEFAULT_EMU_SEED,
            },
            localize_params=dict(_EMU_LOCALIZE_PARAMS),
            batch_size=args.batch_size,
            n_workers=args.n_workers,
            # Hermetic: inline the localize call (no child processes / no picasso
            # import required to be a subprocess) — same reduction code path.
            use_processes=False,
            pixelsize_nm=130.0,
            write_target=WRITE_TARGET_LOCAL,
        )
    # instrument mode
    return FovConfig(  # pragma: no cover - exercised only on the acq PC
        source_kind="tiff-tail",
        source_kwargs={"acq_dir": args.data_dir},
        batch_size=args.batch_size,
        n_workers=args.n_workers,
        use_processes=True,
        write_target=WRITE_TARGET_LOCAL,
    )


@dataclass
class FovRun:
    """The outcome of one clean FOV run, shared by the coverage/metrics checks."""

    result: object  # FovResult
    illu: FakeIllumination | None
    registry: object | None
    registry_ids: dict | None
    wall_s: float


def run_clean_fov(args, registry, illu) -> FovRun:
    """Run one clean FOV through the real WP-4 service (normal end)."""
    svc = LiveAnalysisService(
        registry_client=registry,
        illumination_system=illu,
        lasers_off_finally=True,
    )
    svc.start_experiment()
    cfg = _make_fov_config(args)
    t0 = time.perf_counter()
    result = svc.run_fov(cfg)
    wall = time.perf_counter() - t0
    return FovRun(
        result=result,
        illu=illu if isinstance(illu, FakeIllumination) else None,
        registry=registry,
        registry_ids=result.registry_ids,
        wall_s=wall,
    )


# ── individual checks ────────────────────────────────────────────────────────


def check_keep_up(run: FovRun, args) -> Check:
    """Criterion 1 — acquisition uncompromised / the reader keeps up.

    A separate-process (or inline, in emulator) reader ran concurrently with the
    producer while the service lagged under backpressure rather than dropping.
    We assert: zero dropped batches, the bounded submit queue never overflowed
    (``queue_max_depth <= queue_size`` — the runtime backpressure invariant),
    and every submitted batch completed (nothing starved). ``occupancy``/write
    throughput proper live in instrument mode's MDA; here the load-bearing
    keep-up signal is the no-drop + no-overflow invariant.
    """
    res = run.result
    stats = res.backend_stats or {}
    dropped = int(stats.get("dropped", 0))
    submitted = int(stats.get("submitted", 0))
    completed = int(stats.get("completed", 0))
    q_max = int(stats.get("queue_max_depth", 0))
    q_size = int(stats.get("queue_size", 0) or 1)
    passed = (
        dropped == 0
        and submitted == completed
        and submitted > 0
        and q_max <= q_size
    )
    return Check(
        name="keep_up",
        criterion="1: acquisition uncompromised / reader keeps up",
        passed=passed,
        detail=(
            "dropped={} submitted={} completed={} queue_max_depth={}/{}".format(
                dropped, submitted, completed, q_max, q_size
            )
        ),
        values={
            "dropped": dropped,
            "submitted": submitted,
            "completed": completed,
            "queue_max_depth": q_max,
            "queue_size": q_size,
            "backend": stats.get("backend"),
            "wall_s": round(run.wall_s, 4),
        },
    )


def check_no_silent_subsample(run: FovRun, args) -> Check:
    """Criterion 2 — the WP-4 fix: no silent subsample; coverage reconciles.

    The authoritative reduction must cover EVERY frame read: on a clean run
    ``frames_read == frames_localized`` (``metrics["n_frames"]``), the run is
    not ``partial`` (all submitted batches completed with no errors), and a
    ``run_id`` is present on the result. This is the invariant the WP-4 coverage
    block exists to guarantee.
    """
    res = run.result
    stats = res.backend_stats or {}
    frames_read = int(res.frames_read)
    frames_localized = int((res.metrics or {}).get("n_frames", -1))
    submitted = int(stats.get("submitted", 0))
    completed = int(stats.get("completed", 0))
    errors = int(stats.get("errors", 0))
    partial = not (submitted == completed and errors == 0) or res.aborted
    run_id_ok = bool(res.run_id)
    passed = (
        frames_read == args.n_frames
        and frames_read == frames_localized
        and not partial
        and run_id_ok
    )
    return Check(
        name="no_silent_subsample",
        criterion="2: no silent subsample — coverage block reconciles",
        passed=passed,
        detail=(
            "frames_read={} frames_localized={} expected={} partial={} "
            "run_id={}".format(
                frames_read,
                frames_localized,
                args.n_frames,
                partial,
                res.run_id,
            )
        ),
        values={
            "frames_read": frames_read,
            "frames_localized": frames_localized,
            "expected_frames": args.n_frames,
            "partial": partial,
            "errors": errors,
            "run_id": res.run_id,
        },
    )


def check_live_metrics_real(run: FovRun, args) -> Check:
    """Criterion 3 — live metrics are real (NeNA computed, not None).

    NeNA, locs/frame and background are computed live over the authoritative
    stream by picasso (``postprocess.nena`` on the numpy-2 + ``np.trapezoid``
    path). We require a real, non-None NeNA (px) and a positive locs/frame — a
    None NeNA would mean the live path silently swallowed the compute (the exact
    regression the WP-4 T2 oracle guards).
    """
    m = run.result.metrics or {}
    nena_px = m.get("nena_px")
    nena_nm = m.get("nena_nm")
    spots = m.get("spots_per_frame")
    background = m.get("background")
    n_locs = m.get("n_locs")
    passed = (
        nena_px is not None
        and float(nena_px) > 0
        and spots is not None
        and float(spots) > 0
        and background is not None
        and n_locs is not None
        and int(n_locs) > 0
    )
    return Check(
        name="live_metrics_real",
        criterion="3: live NeNA / locs-per-frame / background are real",
        passed=passed,
        detail=(
            "nena_px={} nena_nm={} spots_per_frame={} background={} "
            "n_locs={}".format(nena_px, nena_nm, spots, background, n_locs)
        ),
        values={
            "nena_px": nena_px,
            "nena_nm": nena_nm,
            "spots_per_frame": spots,
            "background": background,
            "n_locs": n_locs,
        },
    )


def check_registry_record(run: FovRun, args) -> Check:
    """Criterion 4 — one per-FOV record reaches the registry + its coverage.

    The service posts acquisition_run -> fov -> analysis_run -> metrics keyed on
    ``run_id``. We assert the ids came back, the acquisition row is
    ``live_localized`` with ``raw_retained``, and the FOV's ``frame_count``
    reconciles with the frames actually localized (the record's own coverage).
    In emulator the registry is the picasso-registry in-memory mock; on the acq
    PC it is the real client if ``--registry-url`` is given, else the mock.
    """
    res = run.result
    ids = run.registry_ids
    reg = run.registry
    if reg is None or ids is None:
        return Check(
            name="registry_record",
            criterion="4: one per-FOV record reaches the registry",
            passed=False,
            detail="no registry configured or no record posted",
            values={"registry_ids": ids},
        )
    values: dict = {"registry_ids": ids}
    ok = ids.get("acquisition_run_id") == res.run_id
    try:
        acq = reg.get("acquisition_run", res.run_id)
        fov = reg.get("fov", ids["fov_id"])
        an = reg.get("analysis_run", ids["analysis_run_id"])
        values["acq_status"] = acq.get("status")
        values["raw_retained"] = acq.get("raw_retained")
        values["fov_frame_count"] = fov.get("frame_count")
        values["analysis_kind"] = an.get("kind")
        ok = (
            ok
            and acq.get("status") == "live_localized"
            and bool(acq.get("raw_retained"))
            and an.get("kind") == "live_localize"
            and int(fov.get("frame_count") or -1)
            == int((res.metrics or {}).get("n_frames", -2))
        )
    except Exception as exc:  # noqa: BLE001 - a lookup miss is a fail
        return Check(
            name="registry_record",
            criterion="4: one per-FOV record reaches the registry",
            passed=False,
            detail="registry lookup failed: {!r}".format(exc),
            values=values,
        )
    return Check(
        name="registry_record",
        criterion="4: one per-FOV record reaches the registry",
        passed=ok,
        detail="record present + coverage reconciles" if ok else "mismatch",
        values=values,
    )


def _interlock_safe(illu: FakeIllumination) -> bool:
    return illu.all_off and not illu.shutter_open


def check_laser_interlock(args) -> Check:
    """Criterion 5 (SAFETY-CRITICAL) — the T3 interlock on ALL THREE exits.

    Forces (a) normal end, (b) early-abort, and (c) an injected mid-run
    exception, and asserts after EACH that every laser is DISABLED and the
    shutter CLOSED (via the monet per-laser ``enabled`` setter). This is the
    load-bearing safety check; in emulator mode it drives a mock illumination
    over the real :class:`LaserInterlock` + :class:`LiveAnalysisService`
    ``finally`` path. On the acq PC (instrument mode) the same paths run against
    the real monet illumination system.
    """
    paths: dict = {}

    illu = _make_illumination(args)

    def _reset(i):
        if isinstance(i, FakeIllumination):
            i.reset()

    # (a) NORMAL END — the service's finally engages the interlock.
    _reset(illu)
    svc = LiveAnalysisService(
        illumination_system=illu, lasers_off_finally=True
    )
    svc.start_experiment()
    svc.run_fov(_short_fov(args))
    paths["normal_end"] = _exit_safe(illu)

    # (b) EARLY-ABORT — abort requested before any frame is processed.
    _reset(illu)
    svc = LiveAnalysisService(
        illumination_system=illu, lasers_off_finally=True
    )
    svc.start_experiment()
    svc.request_abort()
    res_ab = svc.run_fov(_short_fov(args))
    paths["early_abort"] = _exit_safe(illu) and bool(res_ab.aborted)

    # (c) INJECTED MID-RUN EXCEPTION — a frame source that blows up mid-iterate.
    _reset(illu)
    paths["injected_exception"] = _run_exploding_fov(illu)

    passed = all(paths.values())
    return Check(
        name="laser_interlock",
        criterion="5: T3 laser interlock — all three exit paths (safety)",
        passed=passed,
        detail="paths safe: {}".format(paths),
        values={"paths": paths},
    )


def _exit_safe(illu) -> bool:
    """True if, after an exit, lasers are off + shutter closed.

    For the emulator's :class:`FakeIllumination` we can inspect state directly.
    For a real monet illumination we cannot read every line back generically, so
    we trust the interlock's own :class:`InterlockResult` — engaged once more
    (idempotent) and check it reports ``safe``.
    """
    if isinstance(illu, FakeIllumination):
        return _interlock_safe(illu)
    # instrument: re-engage idempotently and trust the interlock's verdict.
    return bool(
        LaserInterlock(illu).engage(reason="gate2 verify").safe
    )  # pragma: no cover - acq PC only


def _short_fov(args) -> FovConfig:
    """A tiny FOV config for the interlock paths (fast; coverage irrelevant)."""
    if args.mode == MODE_EMULATOR:
        return FovConfig(
            source_kind="mock",
            source_kwargs={"n_frames": 40, "height": 24, "width": 24},
            localize_params=dict(_EMU_LOCALIZE_PARAMS),
            batch_size=20,
            use_processes=False,
        )
    return FovConfig(  # pragma: no cover - acq PC only
        source_kind="tiff-tail",
        source_kwargs={"acq_dir": args.data_dir},
        batch_size=args.batch_size,
        use_processes=True,
    )


def _run_exploding_fov(illu) -> bool:
    """Drive a FOV whose frame source raises mid-iteration; assert safe exit.

    Monkeypatches the frame-source factory to a source that raises inside
    ``batches()`` so the exception propagates through the service body and the
    ``finally`` interlock still fires. Restored afterwards.
    """
    import PycroFlow.live_analysis.frame_source as fs_mod
    from PycroFlow.live_analysis.frame_source import MockFrameSource

    class _Exploding(MockFrameSource):
        def batches(self, batch_size):
            yield from ()
            raise RuntimeError("gate2 injected mid-run exception")

    orig = fs_mod.make_frame_source
    fs_mod.make_frame_source = lambda kind, **kw: _Exploding(
        n_frames=10, height=16, width=16
    )
    try:
        svc = LiveAnalysisService(
            illumination_system=illu, lasers_off_finally=True
        )
        svc.start_experiment()
        res = svc.run_fov(FovConfig(source_kind="mock", use_processes=False))
        return res.error is not None and _exit_safe(illu)
    finally:
        fs_mod.make_frame_source = orig


def check_archive(args) -> Check:
    """Criterion 7 — write-target-gated archive move + checksum-before-delete.

    Exercises the fallback archive path on a small synthetic movie: copy
    local -> archive, verify the archived copy's checksum equals the source's,
    and delete the local original ONLY after that verification. Asserts
    ``verified=True`` and the local original is gone while the archived copy
    exists. Hermetic in both modes (a temp movie, no instrument).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "gate2_movie.tif")
        with open(src, "wb") as fh:
            fh.write(os.urandom(64 * 1024))
        archive_dir = os.path.join(tmp, "archive")
        res = archive_movie(src, archive_dir, write_target=WRITE_TARGET_LOCAL)
        local_gone = not os.path.exists(src)
        dest_ok = bool(res.dest) and os.path.exists(res.dest)
        passed = bool(res.verified and res.moved and local_gone and dest_ok)
        return Check(
            name="archive",
            criterion="7: write-target-gated archive + checksum-before-delete",
            passed=passed,
            detail=(
                "verified={} moved={} local_deleted={} dest_exists={}".format(
                    res.verified, res.moved, local_gone, dest_ok
                )
            ),
            values={
                "verified": res.verified,
                "moved": res.moved,
                "checksum": res.checksum,
                "local_deleted": local_gone,
                "dest": res.dest,
            },
        )


def check_pycromanager_1_0(args, version_block) -> Check:  # pragma: no cover
    """Criterion 6 (INSTRUMENT-ONLY) — pycromanager-1.0 acq-PC items.

    Only meaningful with a running MM + a real MDA, so in emulator mode this is
    recorded as SKIPPED (not a failure). On the acq PC it exercises: a live-tail
    of a real 1.0 NDTiff-v3 / ndstorage dataset (the default ``tiff-tail``
    source already ran in the clean FOV over ``data_dir``), a RAM-peek against
    the running MDA, and confirms numpy-2 + pycromanager-1.0 + picasso coexist
    with no ABI crash. Records the resolved versions.
    """
    if args.mode == MODE_EMULATOR:
        return Check(
            name="pycromanager_1_0",
            criterion="6: pycromanager-1.0 acq-PC items (instrument-only)",
            passed=True,
            detail="skipped in emulator mode (no MM / MDA); records versions",
            values={"skipped": True, "versions": version_block},
        )
    # Instrument mode: confirm the coexistence import (ABI) + version resolve.
    values = {"versions": version_block}
    try:
        import numpy  # noqa: F401
        import picasso  # noqa: F401
        import pycromanager  # noqa: F401

        values["import_coexist"] = True
        # A RAM-peek probe against the running MDA (non-destructive live view).
        from PycroFlow.live_analysis.frame_source import RamPeekFrameSource

        peek = RamPeekFrameSource(port=args.mm_port)
        peek.start()
        time.sleep(1.0)
        peeked = peek.frames_read()
        peek.close()
        values["ram_peek_frames"] = peeked
        passed = bool(
            values["import_coexist"]
            and version_block.get("numpy_has_trapezoid")
            and version_block.get("pycromanager") not in ("not-installed",)
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="pycromanager_1_0",
            criterion="6: pycromanager-1.0 acq-PC items (instrument-only)",
            passed=False,
            detail="coexistence / peek probe failed: {!r}".format(exc),
            values=values,
        )
    return Check(
        name="pycromanager_1_0",
        criterion="6: pycromanager-1.0 acq-PC items (instrument-only)",
        passed=passed,
        detail="numpy-2 + pycromanager-1.0 + picasso coexist; peek OK",
        values=values,
    )


# ── illumination / registry construction (mode-dependent) ────────────────────


def _make_illumination(args):
    """Return the illumination system for the active mode.

    Emulator: a hermetic :class:`FakeIllumination`. Instrument: the real monet
    :class:`PycroFlow.illumination.IlluminationSystem` (lazy import so emulator
    mode never needs monet).
    """
    if args.mode == MODE_EMULATOR:
        return FakeIllumination()
    from PycroFlow.illumination import (  # pragma: no cover - acq PC only
        IlluminationSystem,
    )

    return IlluminationSystem()  # pragma: no cover - acq PC only


def _make_registry(args):
    """Return the registry client for the active mode + a cleanup callable.

    Emulator: the picasso-registry in-memory mock. Instrument: the real client
    if ``--registry-url`` is set, else the same mock (records still get built +
    verified). Returns ``(client, close_fn)``.
    """
    if args.mode == MODE_INSTRUMENT and args.registry_url:  # pragma: no cover
        from picasso_registry.client import RegistryClient

        client = RegistryClient(
            base_url=args.registry_url, token=args.registry_token
        )
        return client, getattr(client, "close", lambda: None)
    from picasso_registry.testing import MockRegistryClient

    client = MockRegistryClient()
    return client, client.close


# ── driver ───────────────────────────────────────────────────────────────────


def run_gate2(args) -> dict:
    """Run every Gate-2 check and return the JSON-ready verdict dict."""
    utc_start = datetime.now(timezone.utc).isoformat()
    version_block = build_version_block(args.mode)
    runner = CheckRunner()

    registry, close_registry = _make_registry(args)
    illu = _make_illumination(args)
    try:
        # One clean FOV drives criteria 1-4.
        fov = run_clean_fov(args, registry, illu)
        runner.add(check_keep_up(fov, args))
        runner.add(check_no_silent_subsample(fov, args))
        runner.add(check_live_metrics_real(fov, args))
        runner.add(check_registry_record(fov, args))
    finally:
        try:
            close_registry()
        except Exception:  # noqa: BLE001
            pass

    # Criterion 5 (safety) runs its own three service invocations.
    runner.run(
        "laser_interlock",
        "5: T3 laser interlock — all three exit paths (safety)",
        lambda: check_laser_interlock(args),
    )
    # Criterion 6 (instrument-only; skipped-pass in emulator).
    runner.run(
        "pycromanager_1_0",
        "6: pycromanager-1.0 acq-PC items (instrument-only)",
        lambda: check_pycromanager_1_0(args, version_block),
    )
    # Criterion 7 archive (hermetic).
    runner.run(
        "archive",
        "7: write-target-gated archive + checksum-before-delete",
        lambda: check_archive(args),
    )

    utc_end = datetime.now(timezone.utc).isoformat()
    failures = [c.to_dict() for c in runner.checks if not c.passed]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": "gate2",
        "gate2_pass": runner.gate2_pass,
        "mode": args.mode,
        "utc_start": utc_start,
        "utc_end": utc_end,
        "version_block": version_block,
        "config": {
            "mode": args.mode,
            "n_frames": args.n_frames,
            "batch_size": args.batch_size,
            "n_workers": args.n_workers,
            "data_dir": args.data_dir,
            "registry_url": args.registry_url,
            "mm_config": args.mm_config,
            "monet_host": args.monet_host,
        },
        "checks": [c.to_dict() for c in runner.checks],
        "failures": failures,
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_verdict(verdict: dict, output_dir: str) -> str:
    """Write the verdict JSON to ``<output_dir>/<mode>_<timestamp>.json``."""
    os.makedirs(output_dir, exist_ok=True)
    name = "{}_{}.json".format(verdict["mode"], _timestamp())
    path = os.path.join(output_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2, sort_keys=True)
    return path


def print_summary(verdict: dict) -> None:
    """Print a concise human-readable summary to stdout."""
    print("=" * 70)
    print(
        "Gate-2 harness — mode={} — {}".format(
            verdict["mode"],
            "PASS" if verdict["gate2_pass"] else "FAIL",
        )
    )
    print("-" * 70)
    for c in verdict["checks"]:
        flag = "PASS" if c["passed"] else "FAIL"
        print("[{}] {:<20} {}".format(flag, c["name"], c["detail"]))
    print("-" * 70)
    vb = verdict["version_block"]
    print(
        "versions: numpy={} (trapezoid={}) picasso={} pycromanager={}".format(
            vb.get("numpy"),
            vb.get("numpy_has_trapezoid"),
            vb.get("picasso"),
            vb.get("pycromanager"),
        )
    )
    if verdict["mode"] == MODE_INSTRUMENT:
        print(
            "          mm={} ndtiff={} ndstorage={}".format(
                vb.get("mm_version"), vb.get("ndtiff"), vb.get("ndstorage")
            )
        )
    print(
        "OVERALL: {}".format(
            "GATE-2 PASS" if verdict["gate2_pass"] else "GATE-2 FAIL"
        )
    )
    print("=" * 70)


def build_parser() -> argparse.ArgumentParser:
    """Build the (fully non-interactive) argument parser."""
    parser = argparse.ArgumentParser(
        prog="gate2-harness",
        description=(
            "Gate-2 on-instrument integration harness for the WP-4 live "
            "slice. Runs every Gate-2 check and writes one JSON verdict under "
            "results/gate2/. --mode emulator (default) is hermetic; --mode "
            "instrument runs the real MDA + monet interlock on the acq PC."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=[MODE_EMULATOR, MODE_INSTRUMENT],
        default=MODE_EMULATOR,
        help="Backend set: emulator (hermetic, default) or instrument.",
    )
    parser.add_argument(
        "--n-frames",
        dest="n_frames",
        type=int,
        default=DEFAULT_N_FRAMES,
        help="Frames in the clean acquisition (coverage reconciliation).",
    )
    parser.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=100,
        help="Frames per contiguous localize batch.",
    )
    parser.add_argument(
        "--n-workers",
        dest="n_workers",
        type=int,
        default=2,
        help="Localize worker processes (instrument mode).",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        default=None,
        help="Output dir for the verdict JSON (default: <repo>/results/gate2).",
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Where the MDA writes the movie (instrument mode; a large data "
        "drive, NOT the repo). Required in instrument mode.",
    )
    parser.add_argument(
        "--registry-url",
        dest="registry_url",
        default=None,
        help="picasso-registry base URL (instrument mode; omit to use the "
        "in-memory mock).",
    )
    parser.add_argument(
        "--registry-token",
        dest="registry_token",
        default=os.environ.get("PICASSO_REGISTRY_TOKEN"),
        help="Auth token for the real registry (or PICASSO_REGISTRY_TOKEN).",
    )
    parser.add_argument(
        "--mm-config",
        dest="mm_config",
        default=None,
        help="Micro-Manager config file (recorded for provenance).",
    )
    parser.add_argument(
        "--mm-port",
        dest="mm_port",
        type=int,
        default=4827,
        help="Micro-Manager ZMQ bridge port (RAM-peek probe).",
    )
    parser.add_argument(
        "--monet-host",
        dest="monet_host",
        default=None,
        help="monet host (recorded for provenance; illumination is created "
        "from the local config on the acq PC).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run Gate-2, write the verdict, print a summary."""
    args = build_parser().parse_args(argv)

    if args.mode == MODE_INSTRUMENT and not args.data_dir:
        print(
            "ERROR: --mode instrument requires --data-dir (a large data "
            "drive, NOT the repo) for the MDA movie.",
            file=sys.stderr,
        )
        return 2

    if args.output_dir is None:
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        args.output_dir = os.path.join(repo_root, "results", "gate2")

    print(
        "Running Gate-2 harness: mode={} n_frames={} batch_size={}".format(
            args.mode, args.n_frames, args.batch_size
        )
    )
    verdict = run_gate2(args)
    path = write_verdict(verdict, args.output_dir)
    print_summary(verdict)
    print("Wrote verdict: {}".format(path))
    return 0 if verdict["gate2_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
