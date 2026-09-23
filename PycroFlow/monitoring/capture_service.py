"""The camera-capture service -- runs in its own OS process.

Spawned by :class:`PycroFlow.monitoring.controller.MonitoringController` (or run
by hand for a smoke test), this process owns all camera I/O so nothing it does
can block or perturb acquisition / fluidics -- the isolation invariant. It:

* runs one :class:`CaptureThread` per camera, each reading its
  :class:`~PycroFlow.monitoring.sources.FrameSource` into a bounded,
  drop-oldest buffer; a slow / raising / downed camera degrades to "no current
  frame" (a black tile panel) and is logged, never raised;
* watches a filesystem control directory for ``round_begin`` / ``round_end`` /
  ``stop`` commands the parent drops (atomically) to bind recording to the
  fluidics round lifecycle;
* while a round window is open, composites the cameras' latest frames into one
  tile at the configured fps and writes them to a per-round ``.avi`` in the
  pool via the wheel-only :class:`~PycroFlow.monitoring.avi.RawAviWriter`;
* on round end, best-effort indexes the clip's URI onto the matching
  ``fluidics_round`` in the registry.

Run standalone::

    python -m PycroFlow.monitoring.capture_service \\
        --config cams.json --control-dir ctl --run-id RUN --emulator
    python -m PycroFlow.monitoring.capture_service \\
        --config cams.json --smoke --instrument   # one-shot camera check
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Optional

from loguru import logger

from PycroFlow.monitoring.avi import RawAviWriter
from PycroFlow.monitoring.config import CameraConfig, MonitoringConfig
from PycroFlow.monitoring.registry_index import RegistryIndexWriter
from PycroFlow.monitoring.sources import make_source
from PycroFlow.monitoring.tiling import compose, plan_layout


class CaptureThread(Thread):
    """Continuously read one camera into a bounded, drop-oldest buffer.

    Isolation lives here: :meth:`get_latest` never blocks and returns ``None``
    when the camera is slow/down, so the compositor renders a black panel
    instead of waiting. All source errors are caught and logged; the thread
    keeps running (a transient hiccup recovers on the next read; a hard failure
    stays a logged gap for the run).
    """

    def __init__(
        self,
        camera: CameraConfig,
        mode: str,
        *,
        queue_size: int,
        fps: int,
        fail_mode: Optional[str] = None,
        fail_delay: float = 0.0,
    ):
        super().__init__(name="cam-{}".format(camera.role), daemon=True)
        self.camera = camera
        self._source = make_source(
            camera, mode, fail_mode=fail_mode, delay=fail_delay
        )
        self._fps = max(1, fps)
        self._buf: deque = deque(maxlen=max(1, queue_size))
        self._lock = Lock()
        # NB: not ``_stop`` -- that name shadows threading.Thread's internal
        # _stop() method and breaks join().
        self._stopped = Event()
        self._down = False  # last-known health, for edge-triggered logging

    def run(self) -> None:
        try:
            self._source.open()
        except Exception as exc:
            logger.warning(
                "monitoring: camera {} did not open ({!r}); recording a gap",
                self.camera.role,
                exc,
            )
            self._down = True
            return
        interval = 1.0 / self._fps
        while not self._stopped.is_set():
            t0 = time.monotonic()
            try:
                frame = self._source.read()
            except Exception as exc:
                self._note_gap(exc)
                self._stopped.wait(interval)
                continue
            if frame is None:
                self._note_gap(None)
                self._stopped.wait(interval)
                continue
            if self._down:
                logger.info(
                    "monitoring: camera {} recovered", self.camera.role
                )
                self._down = False
            with self._lock:
                self._buf.append(frame)
            # Pace reads to ~fps so a healthy emulator camera doesn't spin.
            self._stopped.wait(max(0.0, interval - (time.monotonic() - t0)))

    def _note_gap(self, exc: Optional[Exception]) -> None:
        if not self._down:  # edge-triggered so a dead cam doesn't spam
            logger.warning(
                "monitoring: camera {} produced no frame ({}); recording a gap",
                self.camera.role,
                "unplugged" if exc is None else repr(exc),
            )
            self._down = True

    def get_latest(self):
        """Newest buffered frame, or ``None`` (never blocks)."""
        with self._lock:
            return self._buf[-1] if self._buf else None

    def stop(self) -> None:
        self._stopped.set()

    def close(self) -> None:
        try:
            self._source.close()
        except Exception:  # pragma: no cover - close must not raise
            pass


class CaptureService:
    """Owns the camera threads, the control loop, and the per-round writer."""

    def __init__(
        self,
        config: MonitoringConfig,
        *,
        control_dir: str,
        run_id: str,
        mode: str,
        fail_mode: Optional[str] = None,
        fail_delay: float = 0.0,
    ):
        self.config = config
        self.control_dir = control_dir
        self.run_id = run_id
        self.mode = mode
        self._layout = plan_layout(config.cameras, config.tile_cols)
        self._threads: list[CaptureThread] = [
            CaptureThread(
                cam,
                mode,
                queue_size=config.queue_size,
                fps=config.fps,
                fail_mode=fail_mode,
                fail_delay=fail_delay,
            )
            for cam in config.cameras
        ]
        self._stop = Event()
        self._writer: Optional[RawAviWriter] = None
        self._clip_round: Optional[int] = None
        self._clip_path: Optional[str] = None
        self._clip_name: Optional[str] = None
        self._last_frame = 0.0
        self._index = RegistryIndexWriter.from_env(
            run_id,
            buffer_path=os.path.join(
                config.output_dir, "registry_buffer.sqlite"
            ),
        )

    # -- lifecycle -------------------------------------------------------
    def run(self) -> None:
        os.makedirs(self.config.output_dir, exist_ok=True)
        os.makedirs(self.control_dir, exist_ok=True)
        _prune_retention(self.config.output_dir, self.config.retention_days)
        for t in self._threads:
            t.start()
        tick = min(self.config.poll_interval, 1.0 / max(1, self.config.fps))
        tick = max(0.005, tick / 2.0)
        logger.info(
            "monitoring: capture service up ({} cameras, {} mode, run {})",
            len(self._threads),
            self.mode,
            self.run_id,
        )
        try:
            while not self._stop.is_set():
                self._drain_control()
                if self._stop.is_set():
                    break
                self._maybe_write_frame()
                time.sleep(tick)
        finally:
            self._shutdown()

    def request_stop(self, *_a) -> None:
        self._stop.set()

    # -- control channel -------------------------------------------------
    def _drain_control(self) -> None:
        try:
            names = sorted(
                n for n in os.listdir(self.control_dir) if n.endswith(".json")
            )
        except OSError:
            return
        for name in names:
            path = os.path.join(self.control_dir, name)
            try:
                with open(path) as f:
                    cmd = json.load(f)
            except (OSError, ValueError):
                # A half-written file (should not happen with atomic rename);
                # leave it and retry next tick.
                continue
            try:
                os.remove(path)
            except OSError:
                pass
            self._handle(cmd)
            if self._stop.is_set():
                return

    def _handle(self, cmd: dict) -> None:
        kind = cmd.get("cmd")
        if kind == "round_begin":
            self._open_clip(
                int(cmd.get("round_index", 0)),
                cmd.get("unique_name"),
                cmd.get("t_utc"),
            )
        elif kind == "round_end":
            self._close_clip()
        elif kind == "stop":
            self._stop.set()
        else:
            logger.warning("monitoring: unknown control command {!r}", cmd)

    # -- recording -------------------------------------------------------
    def _open_clip(
        self,
        round_index: int,
        unique_name: Optional[str],
        t_utc: Optional[str],
    ) -> None:
        if self._writer is not None:
            # A begin without an intervening end (dropped end command): finalize
            # the current clip first so we never leak an open writer.
            self._close_clip()
        stamp = t_utc or _utc_stamp()
        name = "run_{}_round{:03d}_{}.avi".format(
            _sanitize(self.run_id), round_index, stamp
        )
        path = os.path.join(self.config.output_dir, name)
        try:
            self._writer = RawAviWriter(
                path,
                width=self._layout.width,
                height=self._layout.height,
                fps=self.config.fps,
            )
        except Exception as exc:  # never let a write failure stall the child
            logger.warning(
                "monitoring: could not open clip {} ({!r}); skipping round {}",
                path,
                exc,
                round_index,
            )
            self._writer = None
            return
        self._clip_round = round_index
        self._clip_name = unique_name
        self._clip_path = path
        self._last_frame = 0.0
        logger.info("monitoring: recording round {} -> {}", round_index, name)

    def _maybe_write_frame(self) -> None:
        if self._writer is None:
            return
        now = time.monotonic()
        if now - self._last_frame < 1.0 / max(1, self.config.fps):
            return
        frames = [t.get_latest() for t in self._threads]
        tile = compose(frames, self._layout)
        try:
            self._writer.write(tile)
        except Exception as exc:  # a write hiccup drops a frame, not the run
            logger.warning("monitoring: dropped a frame ({!r})", exc)
        self._last_frame = now

    def _close_clip(self) -> None:
        if self._writer is None:
            return
        writer, self._writer = self._writer, None
        path, self._clip_path = self._clip_path, None
        rnd, self._clip_round = self._clip_round, None
        name, self._clip_name = self._clip_name, None
        try:
            writer.close()
        except Exception as exc:  # pragma: no cover
            logger.warning("monitoring: failed to finalize clip ({!r})", exc)
            return
        logger.info("monitoring: finished round {} clip {}", rnd, path)
        if path is not None and rnd is not None:
            uri = Path(path).resolve().as_uri()
            self._index.index(rnd, uri, round_name=name)

    def _shutdown(self) -> None:
        self._close_clip()
        for t in self._threads:
            t.stop()
        for t in self._threads:
            t.join(timeout=2.0)
            t.close()
        self._index.close()
        logger.info("monitoring: capture service down")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sanitize(text: str) -> str:
    keep = "-_."
    return "".join(c if c.isalnum() or c in keep else "-" for c in str(text))


def _prune_retention(output_dir: str, retention_days: int) -> None:
    """Delete clips older than ``retention_days`` (0 disables). Best-effort."""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    try:
        entries = os.listdir(output_dir)
    except OSError:
        return
    for name in entries:
        if not name.endswith(".avi"):
            continue
        path = os.path.join(output_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.info("monitoring: pruned old clip {}", name)
        except OSError:
            pass


def _run_smoke(config: MonitoringConfig, mode: str, run_id: str) -> int:
    """Grab a few frames from each camera into one short clip; print the path.

    A no-orchestration sanity check for the instrument leg -- confirms the
    cameras open and frames flow before wiring monitoring into a real run.
    """
    os.makedirs(config.output_dir, exist_ok=True)
    layout = plan_layout(config.cameras, config.tile_cols)
    threads = [
        CaptureThread(c, mode, queue_size=config.queue_size, fps=config.fps)
        for c in config.cameras
    ]
    for t in threads:
        t.start()
    time.sleep(0.5)  # let frames start flowing
    name = "run_{}_smoke_{}.avi".format(_sanitize(run_id), _utc_stamp())
    path = os.path.join(config.output_dir, name)
    with RawAviWriter(
        path, width=layout.width, height=layout.height, fps=config.fps
    ) as w:
        for _ in range(max(1, config.fps)):
            w.write(compose([t.get_latest() for t in threads], layout))
            time.sleep(1.0 / max(1, config.fps))
    for t in threads:
        t.stop()
    for t in threads:
        t.join(timeout=2.0)
        t.close()
    logger.info("monitoring: smoke clip written -> {}", path)
    print(path)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pycroflow-capture",
        description="Fluidics monitoring camera-capture service.",
    )
    parser.add_argument(
        "--config", required=True, help="path to the cameras config JSON"
    )
    parser.add_argument(
        "--control-dir", help="directory the parent drops round commands into"
    )
    parser.add_argument("--run-id", default="run", help="run identifier")
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--emulator",
        dest="mode",
        action="store_const",
        const="emulator",
        help="synthetic frame source (default; for CI / no hardware)",
    )
    src.add_argument(
        "--instrument",
        dest="mode",
        action="store_const",
        const="instrument",
        help="real webcams via OpenCV (needs the [monitoring] extra)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="grab a short clip from each camera and exit (no orchestration)",
    )
    # Fault injection for the isolation proof (emulator source only).
    parser.add_argument("--fail-mode", choices=("slow", "raise", "unplug"))
    parser.add_argument("--fail-delay", type=float, default=0.0)
    args = parser.parse_args(argv)

    with open(args.config) as f:
        config = MonitoringConfig.from_dict(json.load(f))
    mode = args.mode or config.source or "emulator"

    if args.smoke:
        return _run_smoke(config, mode, args.run_id)

    if not args.control_dir:
        parser.error("--control-dir is required unless --smoke is given")

    service = CaptureService(
        config,
        control_dir=args.control_dir,
        run_id=args.run_id,
        mode=mode,
        fail_mode=args.fail_mode,
        fail_delay=args.fail_delay,
    )
    signal.signal(signal.SIGTERM, service.request_stop)
    try:
        service.run()
    except KeyboardInterrupt:  # pragma: no cover
        service.request_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
