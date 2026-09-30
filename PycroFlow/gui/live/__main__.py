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
        help="demo: keep running fresh FOVs until the window is closed "
        "(metrics/advisor keep animating).",
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
        try:
            svc.hub.push_kind(
                "thumbnail",
                svc.run_id,
                data=img.tobytes(),
                shape=img.shape,
                dtype=str(img.dtype),
                pixelsize_nm=130.0,
            )
        except Exception:  # noqa: BLE001
            pass

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


if __name__ == "__main__":
    sys.exit(main())
