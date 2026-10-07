"""WP-LIVE-INT tests: live analysis driven by the orchestrated product path.

Hermetic (emulated systems, in-memory registry stub, thread-pool compute):

* FrameTap — the production-acquisition seam (queue, sentinel, early-abort
  handshake, exception-proof callbacks).
* post_fov_record with a fire-and-forget (BufferedRegistryClient-shaped)
  client: ids are pre-minted so the FK chain never needs server rows.
* The experiment-level payload + posting.
* LiveRunCoordinator — lifecycle, per-FOV runs over the tap, early-abort =
  end-this-FOV + protocol continues, teardown interlock, enablement rules.
* ExperimentService end-to-end: an orchestrated emulated run produces ONE
  run_id across linked experiment/per-FOV registry records; disabled paths
  leave the run unchanged.
"""

from __future__ import annotations

import time
import unittest

from PycroFlow.live_analysis.frame_tap import FrameTap
from PycroFlow.live_analysis.registry_payload import (
    build_experiment_payload,
    post_experiment_record,
    post_fov_record,
)
from PycroFlow.services.experiment_service import (
    ExperimentService,
    ExperimentState,
)
from PycroFlow.services.live_run import LiveRunCoordinator
from PycroFlow.tests import emulators as emu
from PycroFlow.tests.test_live_analysis import FakeIllumination


class _StubRegistry:
    """In-memory log_* client returning rows with ids (RegistryClient-like)."""

    def __init__(self):
        self.experiments = []
        self.acquisitions = []
        self.fovs = []
        self.analyses = []
        self.metrics = []
        self.closed = False

    def _store(self, bucket, fields):
        row = dict(fields)
        row.setdefault("id", "srv-{:d}".format(len(bucket)))
        bucket.append(row)
        return dict(row)

    def log_experiment(self, **fields):
        return self._store(self.experiments, fields)

    def log_acquisition(self, **fields):
        return self._store(self.acquisitions, fields)

    def log_fov(self, **fields):
        return self._store(self.fovs, fields)

    def log_analysis(self, **fields):
        return self._store(self.analyses, fields)

    def log_metrics(self, **fields):
        return self._store(self.metrics, fields)

    def close(self):
        self.closed = True


class _BufferedStubRegistry(_StubRegistry):
    """Fire-and-forget writes, like the WP-3 BufferedRegistryClient."""

    def _store(self, bucket, fields):
        super()._store(bucket, fields)
        return {"buffered": True}  # no row, no id


class TestFrameTap(unittest.TestCase):

    def test_frames_then_sentinel(self):
        tap = FrameTap()
        q = tap.start_fov("fov", {"frames": 2})
        tap.push([1], None)
        tap.push([2], None)
        tap.end_fov()
        self.assertEqual(q.get_nowait(), [1])
        self.assertEqual(q.get_nowait(), [2])
        self.assertIsNone(q.get_nowait())

    def test_fov_end_handshake_clears_on_next_fov(self):
        tap = FrameTap()
        tap.start_fov("a", {})
        self.assertFalse(tap.fov_end_requested())
        tap.request_fov_end()
        self.assertTrue(tap.fov_end_requested())
        tap.start_fov("b", {})
        self.assertFalse(tap.fov_end_requested())

    def test_push_without_fov_is_noop_and_callbacks_never_raise(self):
        tap = FrameTap(on_fov_start=lambda *a: 1 / 0, on_fov_end=lambda: 1 / 0)
        tap.push([1], None)  # no queue yet — ignored
        q = tap.start_fov("fov", {})  # on_fov_start raising is swallowed
        tap.end_fov()  # on_fov_end raising is swallowed
        self.assertIsNone(q.get_nowait())

    def test_backlog_is_bounded_drops_counted_sentinel_delivered(self):
        tap = FrameTap(max_pending_frames=3)
        q = tap.start_fov("fov", {})
        for i in range(5):
            tap.push([i], None)
        self.assertEqual(tap.dropped_frames(), 2)
        tap.end_fov()
        # The three retained frames, then the sentinel — never refused.
        self.assertEqual([q.get_nowait() for _ in range(3)], [[0], [1], [2]])
        self.assertIsNone(q.get_nowait())
        # A fresh FOV re-arms the drop counter.
        tap.start_fov("fov2", {})
        self.assertEqual(tap.dropped_frames(), 0)

    def test_feed_frames_brackets_and_honors_fov_end(self):
        got = {}
        tap = FrameTap(on_fov_start=lambda name, cfg, q: got.update(q=q))
        pushed = tap.feed_frames("fov", {"frames": 3}, [[1], [2], [3]])
        self.assertEqual(pushed, 3)
        drained = [got["q"].get_nowait() for _ in range(4)]
        self.assertEqual(drained, [[1], [2], [3], None])
        # An immediate end-this-FOV request stops the feed but still ends
        # the FOV (sentinel via the finally).
        tap2 = FrameTap()
        tap2._on_fov_start = lambda name, cfg, q: tap2.request_fov_end()
        pushed = tap2.feed_frames("fov", {}, [[1], [2], [3]])
        self.assertEqual(pushed, 0)
        self.assertFalse(tap2.fov_active())


class TestPostRecordsBufferedClient(unittest.TestCase):

    def _payload(self, run_id="RUNID"):
        from PycroFlow.live_analysis.registry_payload import build_fov_payload

        return build_fov_payload(
            run_id=run_id, metrics={"n_frames": 3, "n_locs": 5}
        )

    def test_fov_chain_with_fire_and_forget_client(self):
        client = _BufferedStubRegistry()
        ids = post_fov_record(client, self._payload())
        # Pre-minted ids thread the FK chain without server rows.
        self.assertEqual(ids["acquisition_run_id"], "RUNID")
        for key in ("fov_id", "analysis_run_id", "metrics_id"):
            self.assertTrue(ids[key])
        self.assertEqual(client.fovs[0]["acquisition_run_id"], "RUNID")
        self.assertEqual(client.analyses[0]["fov_id"], ids["fov_id"])
        self.assertEqual(
            client.metrics[0]["analysis_run_id"], ids["analysis_run_id"]
        )

    def test_fov_chain_prefers_server_ids(self):
        client = _StubRegistry()
        ids = post_fov_record(client, self._payload())
        self.assertEqual(ids["fov_id"], client.fovs[0]["id"])

    def test_experiment_payload_and_post(self):
        design = {
            "base_name": "exp1",
            "experiment": {"type": "Exchange"},
        }
        payload = build_experiment_payload(
            experiment_id="EXPID",
            run_id="RUNID",
            design=design,
            setup_name="Emulator",
        )
        self.assertEqual(
            payload,
            {
                "id": "EXPID",
                "extra": {
                    "run_id": "RUNID",
                    "experiment_type": "Exchange",
                    "base_name": "exp1",
                    "setup": "Emulator",
                },
            },
        )
        client = _StubRegistry()
        row = post_experiment_record(client, payload)
        self.assertEqual(row["id"], "EXPID")


class TestAbortGenerations(unittest.TestCase):
    """clear_abort(generation=...) must never erase a newer abort request."""

    def test_generation_gated_clear_keeps_newer_request(self):
        from PycroFlow.live_analysis.service import LiveAnalysisService

        svc = LiveAnalysisService()
        svc.start_experiment()
        svc.request_abort()
        self.assertEqual(svc.abort_generation(), 1)
        svc.clear_abort(generation=0)  # stale clear: no-op
        self.assertTrue(svc.abort_requested())
        svc.request_abort()  # a second request before the re-arm
        svc.clear_abort(generation=1)  # the FOV that consumed request #1
        self.assertTrue(svc.abort_requested())  # request #2 survives
        svc.clear_abort(generation=2)
        self.assertFalse(svc.abort_requested())
        svc.clear_abort()  # unconditional (standalone semantics) still works
        self.assertFalse(svc.abort_requested())


class TestLiveRunCoordinator(unittest.TestCase):

    def _coordinator(self, registry=None, illu=None):
        coord = LiveRunCoordinator()
        coord.registry_client_factory = staticmethod(lambda: registry)
        imaging = emu.EmulatedImagingSystem()
        run_id = coord.start_run(
            imaging_system=imaging,
            illumination_system=illu,
            design={"base_name": "t", "experiment": {"type": "Exchange"}},
            setup_name="Emulator",
        )
        return coord, imaging, run_id

    def _wait_fov_records(self, registry, n, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline and len(registry.analyses) < n:
            time.sleep(0.02)
        self.assertGreaterEqual(len(registry.analyses), n)

    def test_disabled_without_camera_info(self):
        coord = LiveRunCoordinator()
        coord.registry_client_factory = staticmethod(lambda: None)

        class NoInfoImaging:
            pass

        self.assertIsNone(coord.start_run(imaging_system=NoInfoImaging()))
        self.assertIsNone(coord.service)

    def test_disabled_via_env(self):
        import os

        os.environ["PYCROFLOW_LIVE_ANALYSIS"] = "0"
        try:
            coord = LiveRunCoordinator()
            coord.registry_client_factory = staticmethod(lambda: None)
            run_id = coord.start_run(
                imaging_system=emu.EmulatedImagingSystem()
            )
            self.assertIsNone(run_id)
        finally:
            del os.environ["PYCROFLOW_LIVE_ANALYSIS"]

    def test_full_fov_flow_records_linked(self):
        registry = _StubRegistry()
        coord, imaging, run_id = self._coordinator(registry=registry)
        self.assertIsNotNone(run_id)
        self.assertIs(imaging.frame_tap, coord._tap)
        # The experiment row was written up front.
        self.assertEqual(len(registry.experiments), 1)
        exp_id = registry.experiments[0]["id"]
        self.assertEqual(registry.experiments[0]["extra"]["run_id"], run_id)

        # Two acquire entries -> two FOV record chains, all linked.
        imaging._on_entry({"$type": "acquire", "frames": 25, "message": "r1"})
        imaging._on_entry({"$type": "acquire", "frames": 25, "message": "r2"})
        self._wait_fov_records(registry, 2)
        coord.stop_run(wait=True)

        self.assertEqual(len(registry.fovs), 2)
        for acq in registry.acquisitions:
            self.assertEqual(acq["id"], run_id)
            self.assertEqual(acq["experiment_id"], exp_id)
        for fov in registry.fovs:
            self.assertEqual(fov["acquisition_run_id"], run_id)
        # Clean FOVs: coverage honest and complete.
        for analysis in registry.analyses:
            cov = analysis["extra"]["coverage"]
            self.assertFalse(cov["partial"])
            self.assertEqual(cov["frames_missing"], 0)
        self.assertTrue(registry.closed)

    def test_early_abort_ends_fov_and_protocol_continues(self):
        registry = _StubRegistry()
        illu = FakeIllumination()
        coord, imaging, run_id = self._coordinator(
            registry=registry, illu=illu
        )
        service = coord.service
        tap = imaging.frame_tap

        # Mid-FOV early-abort, driven deterministically: play the acquisition
        # ourselves (start_fov / push / end_fov — exactly what record_movie
        # does), aborting strictly between frames.
        from PycroFlow.live_analysis.frame_source import MockFrameSource

        frames = next(MockFrameSource(n_frames=10, seed=0).batches(10)).frames
        tap.start_fov("r1", {"frames": 10000})
        for frame in frames[:5]:
            tap.push(frame, None)
        service.request_abort()
        # The seam relay asked the acquisition to end THIS FOV (that is what
        # stops image_process_fn's frame loop on the instrument).
        self.assertTrue(tap.fov_end_requested())
        tap.end_fov()
        self._wait_fov_records(registry, 1)
        # Early-abort engaged the laser interlock immediately (T3).
        self.assertTrue(illu.lasers_all_off)

        # The protocol continues: the next acquire runs a fresh, clean FOV.
        imaging._on_entry({"$type": "acquire", "frames": 25, "message": "r2"})
        self._wait_fov_records(registry, 2)
        coord.stop_run(wait=True)

        first = registry.analyses[0]["extra"]["coverage"]
        second = registry.analyses[1]["extra"]["coverage"]
        self.assertTrue(first["aborted"])
        self.assertTrue(first["partial"])
        self.assertFalse(second["aborted"])
        self.assertFalse(second["partial"])

    def test_stop_run_engages_end_of_run_interlock(self):
        illu = FakeIllumination()
        coord, imaging, run_id = self._coordinator(illu=illu)
        imaging._on_entry({"$type": "acquire", "frames": 25, "message": "r"})
        coord.stop_run(wait=True)
        # Clean FOVs do NOT engage per-FOV in the orchestrated mode; the
        # end-of-run shutdown does (C21's end-of-run/on-abort scope).
        self.assertTrue(illu.lasers_all_off)
        self.assertIsNone(coord.service)
        self.assertIsNone(imaging.frame_tap)

    def test_no_registry_still_surfaces_records(self):
        coord = LiveRunCoordinator()
        coord.registry_client_factory = staticmethod(lambda: None)
        imaging = emu.EmulatedImagingSystem()
        records = []

        # Intercept hub records: subscribe as soon as the service exists by
        # starting the run first (the experiment record is pushed during
        # start_run, so check it via a fresh coordinator push capture).
        run_id = coord.start_run(imaging_system=imaging)
        self.assertIsNotNone(run_id)
        from PycroFlow.live_analysis.client_seam import CallbackClient

        client = CallbackClient(
            lambda u: records.append(u) if u.kind == "record" else None
        )
        coord.service.hub.add(client)
        imaging._on_entry({"$type": "acquire", "frames": 25, "message": "r"})
        deadline = time.time() + 30
        while time.time() < deadline and not records:
            time.sleep(0.02)
        coord.stop_run(wait=True)
        self.assertTrue(records)
        self.assertFalse(any(r.payload.get("posted") for r in records))


class TestLivePreviewSession(unittest.TestCase):
    """MM-preview mode: metrics + thumbnails with no protocol running."""

    def test_preview_streams_metrics_and_thumbnails_then_stops(self):
        from PycroFlow.live_analysis.client_seam import CallbackClient
        from PycroFlow.services.live_preview import LivePreviewSession

        session = LivePreviewSession()
        # No MM core on the emulated system -> looping MockFrameSource mode.
        service = session.start(emu.EmulatedImagingSystem())
        self.assertIsNotNone(service)
        self.assertTrue(session.active)

        seen = {"metrics": None, "thumbnail": None}
        client = CallbackClient(
            lambda u: (
                seen.update({u.kind: u.payload}) if u.kind in seen else None
            )
        )
        service.hub.add(client)
        deadline = time.time() + 30
        while time.time() < deadline and not (
            seen["metrics"] and seen["thumbnail"]
        ):
            time.sleep(0.05)
        session.stop()
        self.assertTrue(session.wait(timeout=30))
        self.assertFalse(session.active)

        self.assertIsNotNone(seen["metrics"], "no metrics update arrived")
        thumb = seen["thumbnail"]
        self.assertIsNotNone(thumb, "no thumbnail update arrived")
        # The Overview-renderable payload: raw bytes + shape + dtype + the
        # detection-box overlay (flat [x, y, ...] list; may be empty on a
        # spotless frame, but the key is present when detection ran).
        self.assertIn("data", thumb)
        self.assertEqual(len(thumb["shape"]), 2)
        self.assertEqual(thumb["pixelsize_nm"], 130.0)
        self.assertIn("boxes", thumb)
        self.assertEqual(len(thumb["boxes"]) % 2, 0)
        self.assertGreaterEqual(thumb["box_size"], 3)

    def test_early_abort_ends_the_preview(self):
        """The sidebar Early-abort stops the preview outright — no silent
        segment restart with reset metrics."""
        from PycroFlow.services.live_preview import LivePreviewSession

        session = LivePreviewSession()
        service = session.start(emu.EmulatedImagingSystem())
        self.assertIsNotNone(service)
        service.request_abort()  # what the shell's Early-abort issues
        # Both preview threads end on their own (no stop() call needed).
        self.assertTrue(session.wait(timeout=30))
        session.stop()  # GUI reconciliation; idempotent
        self.assertFalse(session.active)

    def test_preview_unavailable_without_camera_info(self):
        from PycroFlow.services.live_preview import LivePreviewSession

        class NoInfoImaging:
            pass

        session = LivePreviewSession()
        self.assertIsNone(session.start(NoInfoImaging()))
        self.assertIsNone(session.start(None))
        self.assertFalse(session.active)


class TestIdentifyBoxes(unittest.TestCase):

    def test_finds_spots_on_a_mock_frame_and_never_raises(self):
        from PycroFlow.live_analysis.boxes import identify_boxes
        from PycroFlow.live_analysis.frame_source import MockFrameSource

        # A deterministic frame from the shared synthetic source (the same
        # frames the demo and the emulated preview draw boxes from).
        frame = next(MockFrameSource(n_frames=1, seed=1).batches(1)).frames[0]
        boxes = identify_boxes(frame, 7, 200)
        self.assertIsNotNone(boxes)
        self.assertGreaterEqual(len(boxes), 2)
        self.assertEqual(len(boxes) % 2, 0)
        h, w = frame.shape
        for x, y in zip(boxes[0::2], boxes[1::2]):
            self.assertTrue(0 <= x < w and 0 <= y < h)
        # Garbage input degrades to None, never an exception.
        self.assertIsNone(identify_boxes(None, 7, 200))


class TestExperimentServiceLiveIntegration(unittest.TestCase):

    _PROTOCOL = {
        "img": {
            "protocol_entries": [
                {
                    "$type": "acquire",
                    "frames": 25,
                    "t_exp": 100,
                    "message": "r1",
                },
                {
                    "$type": "acquire",
                    "frames": 25,
                    "t_exp": 100,
                    "message": "r2",
                },
            ]
        }
    }

    def _run_service(self, registry):
        svc = ExperimentService(imaging_system=emu.EmulatedImagingSystem())
        svc._live.registry_client_factory = staticmethod(lambda: registry)
        svc.load_protocol(dict(self._PROTOCOL))
        svc.start()
        deadline = time.time() + 30
        while time.time() < deadline and not svc.is_finished():
            time.sleep(0.05)
        self.assertTrue(svc.is_finished())
        run_id = svc.live_run_id
        self.assertIsNotNone(svc.live_service)
        svc.end()
        self.assertEqual(svc.state, ExperimentState.FINISHED)
        # end() detaches synchronously; the drain/interlock/close run on the
        # background teardown thread — wait for it before asserting records.
        self.assertIsNone(svc.live_service)
        self.assertTrue(svc._live.wait_idle())
        return svc, run_id

    def test_orchestrated_run_single_run_id_linked_records(self):
        registry = _StubRegistry()
        svc, run_id = self._run_service(registry)

        self.assertEqual(len(registry.experiments), 1)
        exp_id = registry.experiments[0]["id"]
        self.assertEqual(len(registry.fovs), 2)
        self.assertEqual(len(registry.analyses), 2)
        self.assertEqual(len(registry.metrics), 2)
        for acq in registry.acquisitions:
            self.assertEqual(acq["id"], run_id)
            self.assertEqual(acq["experiment_id"], exp_id)
        for fov in registry.fovs:
            self.assertEqual(fov["acquisition_run_id"], run_id)
            self.assertEqual(fov["exposure_ms"], 100)
        for analysis in registry.analyses:
            self.assertEqual(analysis["acquisition_run_id"], run_id)
            cov = analysis["extra"]["coverage"]
            self.assertFalse(cov["partial"])

    def test_registry_disabled_run_unchanged(self):
        svc, run_id = self._run_service(None)
        self.assertIsNotNone(run_id)  # live analysis still ran
        imaging = svc._imaging_system
        self.assertEqual(len(imaging.acquisitions), 2)

    def test_abort_tears_live_analysis_down(self):
        registry = _StubRegistry()
        svc = ExperimentService(imaging_system=emu.EmulatedImagingSystem())
        svc._live.registry_client_factory = staticmethod(lambda: registry)
        svc.load_protocol(dict(self._PROTOCOL))
        svc.start()
        svc.abort()
        self.assertEqual(svc.state, ExperimentState.ABORTED)
        self.assertIsNone(svc.live_service)
        self.assertTrue(svc._live.wait_idle())
        self.assertTrue(registry.closed)


if __name__ == "__main__":
    unittest.main()


class TestPreviewOverlayParams(unittest.TestCase):
    """set_overlay_params steers the box overlay + the next segment's fit."""

    def test_setter_updates_overlay_and_options(self):
        from PycroFlow.services.live_preview import LivePreviewSession

        session = LivePreviewSession()
        session._options = {"localize_params": {"Box Size": 7}}
        session._overlay = {"Box Size": 7, "Min. Net Gradient": 5000}
        session.set_overlay_params(
            {"Box Size": 9, "Min. Net Gradient": 321, "Blur": 1.0}
        )
        self.assertEqual(session._overlay["Box Size"], 9)
        self.assertEqual(session._overlay["Min. Net Gradient"], 321)
        # Folded into the options so the next pipeline segment fits with the
        # same values the boxes use.
        self.assertEqual(
            session._options["localize_params"]["Min. Net Gradient"], 321
        )
        # Safe with no active session state.
        LivePreviewSession().set_overlay_params({"Box Size": 5})
