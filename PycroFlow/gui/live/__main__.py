"""Run the composable operator frontend standalone: ``python -m PycroFlow.gui.live``.

Two modes:

``python -m PycroFlow.gui.live`` (default, **passive**)
    Builds a :class:`~PycroFlow.gui.live.shell.LiveShell` with no ``--service``
    wiring — all panels present, no stream — so the *layout / look & feel* can be
    inspected without an instrument.

``python -m PycroFlow.gui.live --demo`` (**animated, no instrument**)
    Drives the shell with a REAL :class:`~PycroFlow.live_analysis.service.LiveAnalysisService`
    fed by :class:`~PycroFlow.live_analysis.frame_source.MockFrameSource` — the
    genuine WP-4 pipeline (picasso localize on synthetic frames) runs in a
    background thread and streams the SAME payloads a real run would, so every
    panel exercises for real: QC-at-a-glance metrics update, the advisor light
    changes severity, state transitions show in the top bar, the Overview
    animates (a synthetic thumbnail feed), and the sidebar **Early-abort** button
    actually aborts the running FOV. Use it to test *behaviour*, not just looks.

Kept separate from :mod:`PycroFlow.gui.app` (the acquisition main window) because
the operator frontend depends only on WP-4's *seam*, never on the acquisition
backend — so it can run as its own client, including on a monitoring machine.
"""

from __future__ import annotations

import argparse
import sys
import threading


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m PycroFlow.gui.live",
        description="PycroFlow live operator frontend (WP-GUI).",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="Animate the shell with a real LiveAnalysisService + synthetic "
        "(MockFrameSource) frames — no instrument. Exercises every panel.",
    )
    p.add_argument(
        "--frames",
        type=int,
        default=1200,
        help="demo: synthetic frames per FOV (default 1200).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.03,
        help="demo: per-frame delay in s — paces the synthetic acquisition so "
        "updates are watchable (default 0.03 ~ 33 fps).",
    )
    p.add_argument(
        "--seed", type=int, default=5, help="demo: RNG seed (default 5)."
    )
    p.add_argument(
        "--min-net-gradient",
        dest="min_net_gradient",
        type=float,
        default=200.0,
        help="demo: picasso Min. Net Gradient (default 200 — the mock movie "
        "yields plenty of spots; raise it to make spots sparse and trip the "
        "advisor 'sparse' warning).",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="demo/live: keep running fresh FOVs until the window is closed "
        "(metrics/advisor keep animating).",
    )

    live = p.add_argument_group("live (real rig) — with --live")
    live.add_argument(
        "--live",
        action="store_true",
        help="Drive the shell with a REAL pycromanager acquisition + monet "
        "laser feeding the WP-4 service (the Gate-2 clean-FOV path, GUI-"
        "attached). Needs a running Micro-Manager (recent nightly) + the acq-PC "
        "preconditions (PAINT_MONET_TOKEN set, sample loaded, laser calibrated).",
    )
    live.add_argument(
        "--data-dir",
        dest="data_dir",
        help="live: directory for the NDTiff movie (a large data drive, NOT the "
        "repo). Required with --live.",
    )
    live.add_argument(
        "--monet-setup",
        dest="monet_setup",
        help="live: monet.CONFIGS key for this scope (e.g. Mercury_nopf). "
        "Required with --live — the T3 interlock needs it to resolve lasers.",
    )
    live.add_argument(
        "--monet-config-paths",
        dest="monet_config_paths",
        help="live: os.pathsep-separated monet config path(s); exported as "
        "MONET_CONFIG_PATHS before monet imports.",
    )
    live.add_argument(
        "--monet-protocol-paths",
        dest="monet_protocol_paths",
        help="live: monet protocol path(s); exported as MONET_PROTOCOL_PATHS.",
    )
    live.add_argument(
        "--laser",
        type=int,
        help="live: laser line to enable for signal (e.g. 560). Omit to run "
        "dark (no signal). The T3 interlock turns it off at end/abort/crash.",
    )
    live.add_argument(
        "--exposure",
        dest="exposure_ms",
        type=float,
        default=100.0,
        help="live: per-frame exposure ms (default 100).",
    )
    live.add_argument(
        "--pixelsize",
        dest="pixelsize_nm",
        type=float,
        default=130.0,
        help="live: camera pixel size nm for NeNA-in-nm (default 130).",
    )
    live.add_argument(
        "--archive-dir",
        dest="archive_dir",
        default=None,
        help="live: if set, move the movie here after each FOV; default None = "
        "keep in place (a preview run does not archive).",
    )
    live.add_argument(
        "--box-size",
        dest="box_size",
        type=int,
        default=7,
        help="live: picasso Box Size (default 7).",
    )
    live.add_argument(
        "--baseline",
        type=float,
        default=100.0,
        help="live: camera baseline/offset (ADU) for picasso photon conversion.",
    )
    live.add_argument(
        "--sensitivity",
        type=float,
        default=1.0,
        help="live: camera sensitivity (e-/ADU).",
    )
    live.add_argument(
        "--gain", type=float, default=1.0, help="live: camera gain (1 sCMOS)."
    )
    live.add_argument(
        "--qe",
        type=float,
        default=1.0,
        help="live: camera quantum efficiency.",
    )
    live.add_argument(
        "--first-frame-timeout",
        dest="first_frame_timeout_s",
        type=float,
        default=120.0,
        help="live: fail the FOV fast if no first frame arrives within N s.",
    )
    return p


def main(argv=None) -> int:
    try:
        from PyQt6.QtWidgets import QApplication, QMainWindow
    except ImportError:
        sys.stderr.write(
            "PyQt6 is required for the PycroFlow live GUI but is not installed.\n"
            'Install it with:  pip install -e ".[gui]"\n'
        )
        return 2

    args = _build_parser().parse_args(
        argv if argv is not None else sys.argv[1:]
    )

    from PycroFlow.gui.live.theme import apply_live_theme

    # Give Qt only the program name — our flags are argparse's, not Qt's.
    app = QApplication(sys.argv[:1])
    apply_live_theme(app)  # V0.8 look: Fusion base + dark/gold stylesheet.

    if args.live:
        return _run_live(app, QMainWindow, args)

    if args.demo:
        return _run_demo(app, QMainWindow, args)

    from PycroFlow.gui.live.shell import build_live_shell

    shell = build_live_shell()  # passive (no service) — layout inspection.
    win = QMainWindow()
    win.setWindowTitle("PycroFlow — Live QC (operator frontend)")
    win.setCentralWidget(shell)
    win.resize(1200, 800)
    win.show()
    return app.exec()


def _synthetic_frame(rng, h: int = 128, w: int = 128):
    """A DNA-PAINT-ish frame: Poisson background + a few bright blobs (uint16)."""
    import numpy as np

    # Bright blobs on a dim background so it reads clearly at the Overview's
    # default 0..65535 contrast (a real camera frame is darker + relies on the
    # black/white handles or Auto — this is a demo, so bias toward visible).
    img = rng.poisson(600, size=(h, w)).astype(np.float32)
    ys, xs = np.mgrid[0:h, 0:w]
    for _ in range(int(rng.integers(12, 32))):
        cy, cx = rng.integers(4, h - 4), rng.integers(4, w - 4)
        amp = rng.uniform(8000, 30000)
        img += amp * np.exp(
            -(((ys - cy) ** 2 + (xs - cx) ** 2) / (2 * 1.4**2))
        )
    return np.clip(img, 0, 65535).astype(np.uint16)


def _identify_boxes(frame, box, min_ng):
    """Shared identify-for-overlay step (see live_analysis.boxes)."""
    from PycroFlow.live_analysis.boxes import identify_boxes

    return identify_boxes(frame, box, min_ng)


def _run_demo(app, QMainWindow, args) -> int:
    """Drive the shell with the real service + a synthetic stream (no hardware)."""
    import numpy as np
    from PyQt6.QtCore import QTimer

    from PycroFlow.gui.live.shell import build_live_shell
    from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService

    svc = LiveAnalysisService()  # no registry, no illumination — pure demo.
    shell = build_live_shell(service=svc)
    win = QMainWindow()
    win.setWindowTitle("PycroFlow — Live QC (DEMO — synthetic stream)")
    win.setCentralWidget(shell)
    win.resize(1200, 800)

    svc.start_experiment()
    stop = threading.Event()

    def _fov_loop() -> None:
        seed = args.seed
        while not stop.is_set():
            cfg = FovConfig(
                source_kind="mock",
                source_kwargs={
                    "n_frames": args.frames,
                    "height": 64,
                    "width": 64,
                    "seed": seed,
                    "produce_delay_s": args.delay,
                },
                localize_params={
                    "Box Size": 7,
                    "Min. Net Gradient": args.min_net_gradient,
                },
                batch_size=100,
                use_processes=False,  # in-process — cross-platform, no spawn.
                pixelsize_nm=130.0,
            )
            try:
                svc.run_fov(cfg)
            except (
                Exception
            ):  # noqa: BLE001 - a demo FOV error must not crash the UI
                pass
            if not args.loop or stop.is_set():
                break
            seed += 1

    worker = threading.Thread(
        target=_fov_loop, name="live-demo-fov", daemon=True
    )

    # Overview animation: push a synthetic thumbnail on the GUI thread (the
    # service does not emit thumbnails yet — a real WP-4 gap; this stands in so
    # the Overview render path is exercised in the demo).
    rng = np.random.default_rng(args.seed + 1)

    def _push_thumb() -> None:
        if stop.is_set():
            return
        img = _synthetic_frame(rng)
        from PycroFlow.live_analysis.client_seam import push_thumbnail

        push_thumbnail(
            svc.hub,
            svc.run_id,
            img,
            pixelsize_nm=130.0,
            boxes=_identify_boxes(img, 7, args.min_net_gradient),
            box_size=7,
        )

    timer = QTimer()
    timer.timeout.connect(_push_thumb)
    timer.start(500)

    def _cleanup() -> None:
        stop.set()
        svc.request_abort()

    app.aboutToQuit.connect(_cleanup)

    win.show()
    worker.start()
    return app.exec()


def _run_live(app, QMainWindow, args) -> int:
    """Drive the shell with a REAL acquisition + monet laser (the acq PC).

    The GUI-attached twin of the Gate-2 harness's clean FOV: a real pycromanager
    MDA (via the shared AcquisitionDriver) streams frames through the WP-4
    LiveAnalysisService's image-queue source; the T3 interlock (real monet) turns
    the laser off at end/abort/window-close. Runs the FOV(s) on a background
    thread so the GUI stays responsive; the Overview is fed the driver's latest
    raw frame.
    """
    import os

    # Export monet paths BEFORE importing the illumination system (monet reads
    # them at import; override=False means an already-set env still wins).
    if args.monet_config_paths:
        os.environ["MONET_CONFIG_PATHS"] = args.monet_config_paths
    if args.monet_protocol_paths:
        os.environ["MONET_PROTOCOL_PATHS"] = args.monet_protocol_paths

    if not args.data_dir or not args.monet_setup:
        sys.stderr.write(
            "--live needs --data-dir <movie dir> and --monet-setup <name>.\n"
        )
        return 2

    from PyQt6.QtCore import QTimer

    from PycroFlow.gui.live.shell import build_live_shell
    from PycroFlow.illumination import IlluminationSystem
    from PycroFlow.live_analysis.acquisition_driver import (
        AcquisitionDriver,
        image_queue_source_kwargs,
    )
    from PycroFlow.live_analysis.archive import WRITE_TARGET_LOCAL
    from PycroFlow.live_analysis.frame_source import SOURCE_IMAGE_QUEUE
    from PycroFlow.live_analysis.service import FovConfig, LiveAnalysisService

    illu = IlluminationSystem(setup=args.monet_setup)
    svc = LiveAnalysisService(
        illumination_system=illu, lasers_off_finally=True
    )
    shell = build_live_shell(service=svc)
    win = QMainWindow()
    win.setWindowTitle("PycroFlow — Live QC (LIVE — real acquisition)")
    win.setCentralWidget(shell)
    win.resize(1200, 800)

    svc.start_experiment()
    stop = threading.Event()
    driver_ref: dict = {"d": None}
    camera_info = {
        "Baseline": args.baseline,
        "Sensitivity": args.sensitivity,
        "Gain": args.gain,
        "Qe": args.qe,
        "Pixelsize": args.pixelsize_nm or 130.0,
    }

    def _enable_laser() -> None:
        if args.laser is None:
            return
        try:
            illu.set_laser_enabled(int(args.laser), True)
            illu.beampath_open()
        except Exception as exc:  # noqa: BLE001 - non-fatal; logged
            print(
                "WARNING: could not enable laser {}: {!r}".format(
                    args.laser, exc
                )
            )

    def _live_loop() -> None:
        while not stop.is_set():
            drv = AcquisitionDriver(
                args.data_dir,
                args.frames,
                args.exposure_ms,
                enabled=True,
                name="live_raw",
            )
            driver_ref["d"] = drv
            drv.start()
            _enable_laser()
            cfg = FovConfig(
                source_kind=SOURCE_IMAGE_QUEUE,
                source_kwargs=image_queue_source_kwargs(
                    drv, camera_info, args.first_frame_timeout_s
                ),
                localize_params={
                    "Box Size": args.box_size,
                    "Min. Net Gradient": args.min_net_gradient,
                },
                batch_size=100,
                use_processes=True,
                pixelsize_nm=args.pixelsize_nm,
                write_target=WRITE_TARGET_LOCAL,
                movie_source_path=args.data_dir,
                archive_dir=args.archive_dir,
            )
            try:
                svc.run_fov(cfg)  # T3 interlock turns the laser off in finally
            except (
                Exception
            ):  # noqa: BLE001 - a FOV error must not crash the UI
                pass
            drv.close()
            if not args.loop or stop.is_set():
                break

    worker = threading.Thread(
        target=_live_loop, name="live-acq-fov", daemon=True
    )

    # Overview: push the driver's latest raw frame as a thumbnail (~2 Hz). The
    # service emits no thumbnails yet (a real WP-4 gap); this previews the frames
    # actually being acquired.
    def _push_thumb() -> None:
        if stop.is_set():
            return
        drv = driver_ref["d"]
        frame = drv.latest_frame() if drv is not None else None
        if frame is None:
            return
        from PycroFlow.live_analysis.client_seam import push_thumbnail

        push_thumbnail(
            svc.hub,
            svc.run_id,
            frame,
            pixelsize_nm=args.pixelsize_nm or 130.0,
            boxes=_identify_boxes(frame, args.box_size, args.min_net_gradient),
            box_size=args.box_size,
        )

    timer = QTimer()
    timer.timeout.connect(_push_thumb)
    timer.start(500)

    def _cleanup() -> None:
        stop.set()
        svc.request_abort()  # aborts the FOV -> its finally fires the interlock

    app.aboutToQuit.connect(_cleanup)

    win.show()
    worker.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
