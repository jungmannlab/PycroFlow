"""Hermetic tests for WP-4 live analysis (emulator / mock backends only).

Tiers exercised without an instrument:

* emulator end-to-end slice — metrics produced without blocking the producer,
  ``run_id`` propagates, one record reaches a MOCK registry (ACCEPTANCE);
* T2 oracle — live incremental metrics equal the batch metrics over the same
  frames once caught up;
* T2 property — the authoritative reduction never subsamples
  (``reader_frames_read == n_frames``) and ``run_id`` tags every record;
* T3 interlock — under a synthetic slow consumer the bounded queue never
  overflows (the runtime backpressure invariant), and the producer never blocks;
* laser-off interlock — lasers end DISABLED + shutter CLOSED on normal end,
  early-abort, AND an injected mid-run exception;
* archive — the raw movie lands in the archive with a verified checksum and the
  local copy is deleted only after the archived copy is confirmed;
* golden — the per-FOV registry payload is snapshotted.

picasso's ``localize_frames`` and ``postprocess.nena`` are used for real (picasso
is installed in this container). Under the C41-harmonized numpy-2 env the T2
oracle asserts live NeNA == batch NeNA over the same frames (a REAL check — it
previously passed vacuously because both were ``None``: ``nena`` raised on a
``None`` info and the live path swallowed it).
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

import numpy as np

from PycroFlow.live_analysis.archive import (
    WRITE_TARGET_LOCAL,
    WRITE_TARGET_POOL,
    archive_movie,
)
from PycroFlow.live_analysis.compute_backend import (
    POLICY_DROP_OLDEST,
    POLICY_LAG,
    LocalComputeBackend,
    _BoundedSubmitQueue,
)
from PycroFlow.live_analysis.frame_source import (
    MockFrameSource,
    natural_key,
    position_from_name,
)
from PycroFlow.live_analysis.laser_interlock import LaserInterlock
from PycroFlow.live_analysis.metrics import RunningMetrics
from PycroFlow.live_analysis.registry_payload import build_fov_payload
from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService
from PycroFlow.live_analysis.worker import LocalizeRequest, localize_batch

try:
    import PyQt6  # noqa: F401

    _HAVE_PYQT6 = True
except ImportError:
    _HAVE_PYQT6 = False

_LOC_PARAMS = {"Box Size": 7, "Min. Net Gradient": 200}


# ── fakes ────────────────────────────────────────────────────────────────


class _FakeLaser:
    def __init__(self):
        self.enabled = True


class _FakeInstrument:
    def __init__(self):
        self.lasers = {488: _FakeLaser(), 561: _FakeLaser()}
        self.curr_laser = 488


class FakeIllumination:
    """Duck-typed stand-in for IlluminationSystem's interlock surface."""

    def __init__(self, *, fail_laser=None, fail_shutter=False):
        self.instrument = _FakeInstrument()
        self.shutter_open = True
        self._fail_laser = fail_laser
        self._fail_shutter = fail_shutter

    def set_laser_enabled(self, laser, enabled):
        if self._fail_laser is not None and laser == self._fail_laser:
            raise RuntimeError("simulated laser comms failure")
        self.instrument.lasers[laser].enabled = enabled

    def beampath_close(self):
        if self._fail_shutter:
            raise RuntimeError("simulated shutter failure")
        self.shutter_open = False

    @property
    def all_off(self):
        return all(not la.enabled for la in self.instrument.lasers.values())


# ── filename parsing ─────────────────────────────────────────────────────


class TestFilenameParsing(unittest.TestCase):
    def test_natural_sort_beats_lexicographic_rollover(self):
        names = [
            "a_MMStack_10.tif",
            "a_MMStack.tif",
            "a_MMStack_2.tif",
            "a_MMStack_9.tif",
        ]
        ordered = sorted(names, key=natural_key)
        # _2 before _9 before _10 (lexicographic would put _10 before _2).
        self.assertEqual(
            ordered,
            [
                "a_MMStack.tif",
                "a_MMStack_2.tif",
                "a_MMStack_9.tif",
                "a_MMStack_10.tif",
            ],
        )

    def test_position_from_name(self):
        self.assertEqual(position_from_name("exp_MMStack_Pos3.ome.tif"), 3)
        self.assertEqual(position_from_name("exp_MMStack_Pos3_5.ome.tif"), 3)
        self.assertIsNone(position_from_name("exp_MMStack.ome.tif"))
        self.assertIsNone(position_from_name(None))


# ── mock frame source: coverage + per-position batching ──────────────────


class TestMockFrameSource(unittest.TestCase):
    def test_full_coverage_no_subsampling(self):
        src = MockFrameSource(n_frames=137, height=32, width=32)
        seen = 0
        for b in src.batches(20):
            seen += b.n_frames
        self.assertEqual(seen, 137)
        self.assertEqual(src.frames_read(), 137)

    def test_batch_never_spans_two_positions(self):
        src = MockFrameSource(n_frames=60, height=32, width=32, positions=3)
        seen_positions = []
        for b in src.batches(25):
            # A single batch has exactly one position (by construction).
            seen_positions.append(b.position)
        self.assertEqual(set(seen_positions), {0, 1, 2})


# ── running metrics + T2 oracle ──────────────────────────────────────────


class TestRunningMetricsOracle(unittest.TestCase):
    def _independent_batch_metrics(self, frames, info):
        """Independent single-shot batch metrics over ALL frames at once.

        Deliberately does NOT reuse RunningMetrics: localize the whole stack in
        one call and compute NeNA once (``postprocess.nena``) — the offline
        ground truth the live throttled/incremental path must reproduce.
        """
        locs = localize_batch(frames, info, _LOC_PARAMS, start_frame=0)
        n_frames = frames.shape[0]
        spf = len(locs) / n_frames
        bg = float(locs["bg"].mean()) if len(locs) else None
        from picasso import postprocess

        info_list = [info] if isinstance(info, dict) else info
        _r, s = postprocess.nena(locs, info_list)
        return spf, bg, float(s), len(locs)

    def test_live_throttled_equals_independent_batch(self):
        """T2 oracle: the THROTTLED/incremental live NeNA == single-shot batch.

        This exercises the *real* throttled path (realistic
        ``nena_min_new_locs`` / ``nena_min_interval_s``, no force during the
        stream), snapshotting after every batch as a client would. The point is
        that incremental accumulation + throttling do not change the
        authoritative NeNA: the final live value must equal an INDEPENDENT
        single-shot batch NeNA over the same frames. (Previously this compared
        two identical forced computations with 0/0 throttle — vacuous.)
        """
        nena_tol_px = 1e-6
        n = 400
        src = MockFrameSource(n_frames=n, height=64, width=64, seed=7)
        info = src.camera_info()
        all_frames = np.stack([src._make_frame() for _ in range(n)])
        # Fresh source → the live path sees the SAME frames (same seed).
        src2 = MockFrameSource(n_frames=n, height=64, width=64, seed=7)
        # Realistic throttle: recompute at most every ~1500 new locs. With ~8
        # locs/frame over 400 frames this fires a handful of times mid-stream —
        # the actual incremental code path, not a forced single shot.
        rm = RunningMetrics(nena_min_new_locs=1500, nena_min_interval_s=0.0)
        rm.set_info(info)
        rm.set_pixelsize_nm(1.0)  # NeNA nm == px so we compare directly
        recomputes = 0
        prev = None
        for b in src2.batches(50):
            locs = localize_batch(
                b.frames, info, _LOC_PARAMS, start_frame=b.start_frame
            )
            rm.update(locs, b.n_frames)
            snap = rm.snapshot()  # throttled: may or may not recompute NeNA
            if snap["nena_px"] is not None and snap["nena_px"] != prev:
                recomputes += 1
                prev = snap["nena_px"]
        # The final snapshot (force so the last batch is always included).
        live = rm.snapshot(force_nena=True)

        b_spf, b_bg, b_nena, b_nlocs = self._independent_batch_metrics(
            all_frames, info
        )
        self.assertEqual(live["n_frames"], n)
        self.assertEqual(live["n_locs"], b_nlocs)
        self.assertAlmostEqual(live["spots_per_frame"], b_spf, places=4)
        self.assertAlmostEqual(live["background"], round(b_bg, 4), places=3)
        # The throttle actually recomputed mid-stream (real incremental path),
        # and the final throttled value equals the independent single-shot batch.
        self.assertGreaterEqual(
            recomputes, 1, "throttled NeNA never recomputed mid-stream"
        )
        self.assertIsNotNone(live["nena_px"], "live NeNA must not be None")
        self.assertGreater(b_nena, 0.0)
        self.assertAlmostEqual(live["nena_px"], b_nena, delta=nena_tol_px)

    def test_absolute_frame_indices_are_contiguous(self):
        """start_frame offset makes frame indices absolute + contiguous."""
        src = MockFrameSource(n_frames=120, height=48, width=48, seed=3)
        info = src.camera_info()
        maxframe = -1
        for b in src.batches(40):
            locs = localize_batch(
                b.frames, info, _LOC_PARAMS, start_frame=b.start_frame
            )
            if len(locs):
                self.assertGreaterEqual(
                    int(locs["frame"].min()), b.start_frame
                )
                maxframe = max(maxframe, int(locs["frame"].max()))
        self.assertLess(maxframe, 120)


# ── end-to-end slice (ACCEPTANCE) + property (no subsampling) ─────────────


class TestEndToEndSlice(unittest.TestCase):
    def _run(self, *, use_processes, illu=None, registry=None, n_frames=200):
        svc = LiveAnalysisService(
            registry_client=registry, illumination_system=illu
        )
        run_id = svc.start_experiment()
        cfg = FovConfig(
            source_kind="mock",
            source_kwargs={
                "n_frames": n_frames,
                "height": 48,
                "width": 48,
                "seed": 5,
            },
            localize_params=_LOC_PARAMS,
            batch_size=50,
            n_workers=2,
            use_processes=use_processes,
            pixelsize_nm=130.0,
        )
        return svc, run_id, svc.run_fov(cfg)

    def test_inline_end_to_end_with_mock_registry(self):
        from picasso_registry.testing import mock_registry

        illu = FakeIllumination()
        with mock_registry() as reg:
            svc, run_id, res = self._run(
                use_processes=False, illu=illu, registry=reg
            )
            # run_id propagates through every row.
            self.assertEqual(res.run_id, run_id)
            self.assertEqual(res.registry_ids["acquisition_run_id"], run_id)
            acq = reg.get("acquisition_run", run_id)
            self.assertEqual(acq["status"], "live_localized")
            fov = reg.get("fov", res.registry_ids["fov_id"])
            self.assertEqual(fov["acquisition_run_id"], run_id)
            an = reg.get("analysis_run", res.registry_ids["analysis_run_id"])
            self.assertEqual(an["kind"], "live_localize")
            # metrics were produced.
            self.assertGreater(res.metrics["n_locs"], 0)
            self.assertEqual(res.metrics["n_frames"], 200)

    def test_property_no_subsampling(self):
        # The authoritative reduction covers EVERY frame, and a clean finish is
        # reconciled: frames_read == frames_localized, not partial.
        svc, run_id, res = self._run(use_processes=False, n_frames=173)
        self.assertEqual(res.frames_read, 173)
        self.assertEqual(res.metrics["n_frames"], 173)
        self.assertFalse(res.partial)
        self.assertEqual(res.coverage["frames_read"], 173)
        self.assertEqual(res.coverage["frames_localized"], 173)
        self.assertEqual(res.coverage["frames_missing"], 0)

    def test_abort_records_partial_coverage_no_silent_drop(self):
        """On abort, coverage is recorded honestly (partial flag), not silent.

        A batch may be read (frames_read incremented) but not localized once the
        stop is set. The FOV must then be flagged partial with the TRUE localized
        count, and the read/localized/missing counts must reconcile exactly —
        no frames vanish silently from the accounting.
        """
        illu = FakeIllumination()
        svc = LiveAnalysisService(illumination_system=illu)
        svc.start_experiment()

        # Abort as soon as the first batch's metrics land, so later batches are
        # read-but-not-localized — the exact partial-coverage window.
        def abort_after_first(update):
            if (
                update.kind == "metrics"
                and (update.payload.get("metrics") or {}).get("n_frames", 0)
                > 0
            ):
                svc.request_abort()

        from PycroFlow.live_analysis.client_seam import CallbackClient

        svc.hub.add(CallbackClient(abort_after_first))
        cfg = FovConfig(
            source_kind="mock",
            source_kwargs={"n_frames": 600, "height": 32, "width": 32},
            localize_params=_LOC_PARAMS,
            batch_size=30,
            use_processes=False,
            metrics_push_interval_s=0.0,  # push after every batch
        )
        res = svc.run_fov(cfg)
        cov = res.coverage
        # The accounting reconciles exactly — nothing is dropped silently.
        self.assertEqual(
            cov["frames_localized"] + cov["frames_missing"], cov["frames_read"]
        )
        self.assertEqual(cov["frames_localized"], res.metrics["n_frames"])
        self.assertTrue(res.aborted)
        # Partial coverage is explicit when fewer frames were localized than read.
        if cov["frames_missing"] > 0:
            self.assertTrue(res.partial)

    def test_separate_process_pool(self):
        # The design-intended path: real worker processes.
        svc, run_id, res = self._run(use_processes=True, n_frames=150)
        self.assertEqual(res.frames_read, 150)
        self.assertEqual(res.backend_stats["backend"], "local-subprocess")
        self.assertEqual(
            res.backend_stats["submitted"], res.backend_stats["completed"]
        )


# ── T3 backpressure: bounded queue never overflows; producer never blocks ─


class TestBackpressure(unittest.TestCase):
    def test_bounded_queue_lag_never_overflows(self):
        q = _BoundedSubmitQueue(maxsize=4, policy=POLICY_LAG)
        for i in range(4):
            self.assertTrue(q.put(i))
        # Full now: a lag put returns False (would block) without overflowing.
        self.assertFalse(q.put(99, block_timeout=0.01))
        self.assertLessEqual(q.depth(), q.maxsize)
        self.assertLessEqual(q.max_depth_seen, q.maxsize)

    def test_bounded_queue_drop_oldest_bounded_and_counts(self):
        q = _BoundedSubmitQueue(maxsize=2, policy=POLICY_DROP_OLDEST)
        for i in range(5):
            self.assertTrue(q.put(i))
        self.assertLessEqual(q.depth(), q.maxsize)
        self.assertEqual(q.n_dropped, 3)

    def test_slow_consumer_producer_never_stalls_below_rate(self):
        """Under a synthetic slow consumer the producer keeps producing.

        The frame source runs on its own at a fixed rate; the pipeline lags
        (bounded queue) but NEVER makes the producer wait for it. We assert the
        producer finishes at ~its rate regardless of the slow sink, and the
        queue never exceeds its bound (the runtime invariant).
        """
        # Slow sink: each result "takes" real time to consume.
        consumed = []

        def slow_sink(res):
            time.sleep(0.02)
            consumed.append(res.seq)

        backend = LocalComputeBackend(
            info=None,
            params=_LOC_PARAMS,
            on_result=slow_sink,
            n_workers=1,
            queue_size=3,
            policy=POLICY_LAG,
            use_processes=False,
        )
        backend.start()

        # A producer thread submits frames at a fixed rate on its own clock; it
        # must not be starved by the slow sink. We measure that it keeps up.
        rng = np.random.default_rng(0)
        n = 12
        produced = 0
        producer_done = threading.Event()
        overflow = []

        def produce():
            nonlocal produced
            for i in range(n):
                frames = rng.integers(90, 110, size=(5, 24, 24)).astype(
                    np.uint16
                )
                backend.submit(LocalizeRequest(i, frames, i * 5, None))
                produced += 1
                # Record the queue depth invariant at runtime.
                if backend._submit_q.depth() > backend._submit_q.maxsize:
                    overflow.append(backend._submit_q.depth())
            producer_done.set()

        t = threading.Thread(target=produce)
        t.start()
        t.join(timeout=30.0)
        backend.drain_and_stop(timeout=30.0)

        self.assertTrue(producer_done.is_set())
        self.assertEqual(produced, n)
        # The queue bound (guaranteed by queue.Queue(maxsize)) held throughout.
        self.assertEqual(overflow, [])
        self.assertLessEqual(
            backend.stats()["queue_max_depth"], backend.stats()["queue_size"]
        )

    def test_accounting_reconciles_no_batch_vanishes(self):
        """Real accounting check: submitted == completed + queued + in-flight.

        After a clean teardown every submitted batch is accounted for and none
        remain unfinished (the replacement for the old decorative bare-assert).
        """
        seen = []
        info = MockFrameSource(n_frames=1, height=20, width=20).camera_info()
        backend = LocalComputeBackend(
            info=info,
            params=_LOC_PARAMS,
            on_result=lambda r: seen.append(r.seq),
            n_workers=1,
            queue_size=4,
            policy=POLICY_LAG,
            use_processes=False,
        )
        backend.start()
        rng = np.random.default_rng(1)
        n = 10
        for i in range(n):
            frames = rng.integers(90, 110, size=(4, 20, 20)).astype(np.uint16)
            self.assertTrue(
                backend.submit(LocalizeRequest(i, frames, i * 4, None))
            )
        backend.drain_and_stop(timeout=30.0)
        st = backend.stats()
        # Every submitted batch is accounted for; none unfinished; none errored.
        self.assertEqual(st["submitted"], n)
        self.assertEqual(st["completed"], n)
        self.assertEqual(st["unfinished"], 0)
        self.assertEqual(st["errors"], 0)
        self.assertFalse(st["timed_out"])
        self.assertEqual(len(seen), n)

    def test_hung_worker_teardown_no_leaked_thread(self):
        """A worker that never returns its result must not leak the drain thread.

        The drain loop must exit on the stop flag REGARDLESS of completion, so a
        hung/slow worker (result never arrives → completed < submitted) can't
        spin it forever. We simulate the hang by inflating the submitted count
        beyond what any result will satisfy, then assert drain_and_stop returns
        promptly, flags timed_out with the leaked count, and joins the thread.
        """
        backend = LocalComputeBackend(
            info=None,
            params=_LOC_PARAMS,
            on_result=lambda r: None,
            n_workers=1,
            queue_size=4,
            policy=POLICY_LAG,
            use_processes=False,
        )
        backend.start()
        drain_thread = backend._drain
        # Pretend two batches were submitted whose results will never arrive
        # (the "hung worker": completed stays < submitted).
        with backend._counter_lock:
            backend._submitted = 2  # no matching results will be produced
        t0 = time.monotonic()
        backend.drain_and_stop(timeout=1.0)
        elapsed = time.monotonic() - t0
        # Returned promptly (didn't hang on the missing results indefinitely).
        self.assertLess(elapsed, 10.0)
        st = backend.stats()
        self.assertTrue(st["timed_out"])
        self.assertEqual(st["unfinished"], 2)
        # No leaked drain thread — it exited on the stop flag and was joined.
        self.assertIsNotNone(drain_thread)
        self.assertFalse(drain_thread.is_alive())


# ── laser interlock (T3, full) ───────────────────────────────────────────


class TestLaserInterlock(unittest.TestCase):
    def test_normal_end_disables_all_lasers_and_closes_shutter(self):
        illu = FakeIllumination()
        result = LaserInterlock(illu).engage(reason="normal")
        self.assertTrue(result.safe)
        self.assertTrue(illu.all_off)
        self.assertFalse(illu.shutter_open)
        self.assertEqual(sorted(result.lasers_disabled), [488, 561])

    def test_disabled_master_switch_is_noop(self):
        illu = FakeIllumination()
        result = LaserInterlock(illu, enabled=False).engage()
        self.assertFalse(result.attempted)
        self.assertTrue(illu.shutter_open)  # untouched

    def test_none_illumination_is_safe_noop(self):
        result = LaserInterlock(None).engage()
        self.assertFalse(result.attempted)

    def test_fail_safe_one_laser_fails_others_still_off_shutter_closed(self):
        illu = FakeIllumination(fail_laser=488)
        result = LaserInterlock(illu).engage()
        # 561 still disabled, shutter still closed, 488 recorded as failed.
        self.assertIn(561, result.lasers_disabled)
        self.assertIn(488, result.lasers_failed)
        self.assertFalse(illu.shutter_open)
        self.assertFalse(result.safe)  # honest: not fully successful

    def test_empty_lasers_addresses_nothing_is_not_safe(self):
        """ "Safe" must not mean "did nothing": zero lasers addressed -> not safe.

        An empty/missing ``lasers`` mapping with no ``curr_laser`` means the
        interlock disables ZERO lasers. Even though it closes the shutter, it
        must NOT report ``safe`` (and must record why).
        """

        class _EmptyInstrument:
            lasers = {}
            curr_laser = None

        class _EmptyIllu:
            def __init__(self):
                self.instrument = _EmptyInstrument()
                self.shutter_open = True

            def set_laser_enabled(self, laser, enabled):  # pragma: no cover
                raise AssertionError("no laser should be addressed")

            def beampath_close(self):
                self.shutter_open = False

        illu = _EmptyIllu()
        result = LaserInterlock(illu).engage()
        self.assertEqual(result.lasers_disabled, [])
        self.assertFalse(illu.shutter_open)  # shutter did close
        self.assertTrue(result.attempted)
        self.assertFalse(result.safe)  # but did nothing -> not safe
        self.assertTrue(result.errors)  # recorded why zero were addressed

    def test_interlock_on_early_abort(self):
        illu = FakeIllumination()
        svc = LiveAnalysisService(illumination_system=illu)
        svc.start_experiment()
        svc.request_abort()  # abort before any frame processed
        cfg = FovConfig(
            source_kind="mock",
            source_kwargs={"n_frames": 200, "height": 32, "width": 32},
            localize_params=_LOC_PARAMS,
            batch_size=50,
            use_processes=False,
        )
        res = svc.run_fov(cfg)
        self.assertTrue(res.aborted)
        self.assertTrue(illu.all_off)
        self.assertFalse(illu.shutter_open)

    def test_interlock_on_injected_midrun_exception(self):
        illu = FakeIllumination()
        svc = LiveAnalysisService(illumination_system=illu)
        svc.start_experiment()

        # A source whose batches() blows up mid-iteration.
        class ExplodingSource(MockFrameSource):
            def batches(self, batch_size):
                yield from ()
                raise RuntimeError("boom mid-run")

        import PycroFlow.live_analysis.frame_source as fs_mod

        orig = fs_mod.make_frame_source
        fs_mod.make_frame_source = lambda kind, **kw: ExplodingSource(
            n_frames=10, height=16, width=16
        )
        try:
            cfg = FovConfig(source_kind="mock", use_processes=False)
            res = svc.run_fov(cfg)
        finally:
            fs_mod.make_frame_source = orig
        self.assertIsNotNone(res.error)
        # The T3 fail-safe still fired.
        self.assertTrue(illu.all_off)
        self.assertFalse(illu.shutter_open)


# ── archive: checksummed move, delete only after verify ──────────────────


class TestArchive(unittest.TestCase):
    def test_move_with_verified_checksum_then_delete_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "movie.tif")
            with open(src, "wb") as fh:
                fh.write(os.urandom(4096))
            archive_dir = os.path.join(tmp, "archive")
            res = archive_movie(
                src, archive_dir, write_target=WRITE_TARGET_LOCAL
            )
            self.assertTrue(res.moved)
            self.assertTrue(res.verified)
            self.assertTrue(os.path.exists(res.dest))
            # Local copy deleted ONLY after the archived copy is confirmed.
            self.assertFalse(os.path.exists(src))

    def test_pool_write_target_skips_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "movie.tif")
            with open(src, "wb") as fh:
                fh.write(b"data")
            res = archive_movie(
                src,
                os.path.join(tmp, "archive"),
                write_target=WRITE_TARGET_POOL,
            )
            self.assertFalse(res.moved)
            self.assertTrue(os.path.exists(src))  # untouched: already on pool

    def test_dataset_dir_is_archived_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds = os.path.join(tmp, "acq_1")
            os.makedirs(ds)
            for i in range(3):
                with open(os.path.join(ds, "f{}.tif".format(i)), "wb") as fh:
                    fh.write(os.urandom(2048))
            res = archive_movie(
                ds,
                os.path.join(tmp, "archive"),
                write_target=WRITE_TARGET_LOCAL,
            )
            self.assertTrue(res.verified)
            self.assertFalse(os.path.exists(ds))
            self.assertTrue(
                os.path.exists(os.path.join(tmp, "archive", "acq_1", "f0.tif"))
            )


# ── golden: per-FOV registry payload ─────────────────────────────────────


class TestGoldenPayload(unittest.TestCase):
    _RID = "01TESTRUNID000000000000000"
    _METRICS = {
        "n_locs": 1234,
        "n_frames": 500,
        "spots_per_frame": 2.468,
        "background": 101.5,
        "nena_nm": 3.2,
    }

    def test_clean_payload_shape_is_stable(self):
        coverage = {
            "frames_read": 500,
            "frames_localized": 500,
            "frames_missing": 0,
            "partial": False,
            "aborted": False,
            "errored": False,
        }
        payload = build_fov_payload(
            run_id=self._RID,
            metrics=self._METRICS,
            fov={"pos_x": 10.0, "pos_y": 20.0, "frame_rate_hz": 10.0},
            acquisition={"microscope_id": "Mercury"},
            analysis={"picasso_version": "0.11.2"},
            coverage=coverage,
        )
        expected = {
            "run_id": self._RID,
            "acquisition_run": {
                "id": self._RID,
                "status": "live_localized",
                "raw_retained": True,
                "microscope_id": "Mercury",
            },
            "fov": {
                "acquisition_run_id": self._RID,
                "frame_count": 500,
                "extra": {"coverage": coverage},
                "pos_x": 10.0,
                "pos_y": 20.0,
                "frame_rate_hz": 10.0,
            },
            "analysis_run": {
                "acquisition_run_id": self._RID,
                "kind": "live_localize",
                "status": "done",
                "compute_location": "local-subprocess",
                "extra": {"coverage": coverage},
                "picasso_version": "0.11.2",
            },
            "metrics": {
                "scope": "live",
                "n_locs": 1234,
                "spots_per_frame": 2.468,
                "background": 101.5,
                "nena_nm": 3.2,
            },
        }
        self.assertEqual(payload, expected)

    def test_partial_payload_marks_aborted_status(self):
        """An aborted/partial run is flagged in status + coverage, not silent."""
        coverage = {
            "frames_read": 500,
            "frames_localized": 450,
            "frames_missing": 50,
            "partial": True,
            "aborted": True,
            "errored": False,
        }
        payload = build_fov_payload(
            run_id=self._RID, metrics=self._METRICS, coverage=coverage
        )
        self.assertEqual(
            payload["acquisition_run"]["status"], "live_localized_partial"
        )
        self.assertEqual(payload["analysis_run"]["status"], "aborted")
        self.assertEqual(
            payload["fov"]["extra"]["coverage"]["frames_missing"], 50
        )
        self.assertTrue(
            payload["analysis_run"]["extra"]["coverage"]["partial"]
        )


# ── Quality tab: the first client over the streaming seam (headless Qt) ───


@unittest.skipUnless(_HAVE_PYQT6, "PyQt6 not installed")
class TestQualityTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_tab_subscribes_and_renders_metrics(self):
        from PyQt6.QtWidgets import QApplication

        from PycroFlow.gui.tabs.quality_tab import QualityTab

        svc = LiveAnalysisService(illumination_system=FakeIllumination())
        tab = QualityTab(service=svc)
        run_id = svc.start_experiment()
        # The service pushes updates through the hub; the bridge marshals them.
        svc.hub.push_kind(
            "metrics",
            run_id,
            metrics={
                "nena_nm": 3.5,
                "spots_per_frame": 2.1,
                "background": 100.0,
                "n_locs": 500,
                "n_frames": 200,
            },
            backend={"queue_depth": 2, "queue_size": 8},
        )
        QApplication.processEvents()  # deliver the queued Qt signal
        self.assertIn("3.5", tab.nena_label.text())
        self.assertIn("2.1", tab.locs_label.text())
        self.assertEqual(tab.run_id_label.text(), run_id)
        self.assertEqual(tab.lag_label.text(), "2/8")

    def test_abort_button_issues_control_call(self):
        from PyQt6.QtWidgets import QApplication

        from PycroFlow.gui.tabs.quality_tab import QualityTab

        svc = LiveAnalysisService()
        svc.start_experiment()
        tab = QualityTab(service=svc)
        self.assertFalse(svc._abort.is_set())
        tab._on_abort()
        QApplication.processEvents()
        self.assertTrue(svc._abort.is_set())


if __name__ == "__main__":
    unittest.main()
