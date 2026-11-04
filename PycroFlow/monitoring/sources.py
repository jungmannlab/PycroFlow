"""Frame sources for the capture service.

Two sources present an identical ``open`` / ``read`` / ``close`` surface so
the capture pipeline is byte-for-byte the same in both modes -- only the
source differs, per the work order:

* :class:`EmulatedFrameSource` -- synthetic frames in pure ``numpy`` (no
  camera, no OpenCV), for hermetic CI. Supports deliberate fault injection
  (slow / raising / "unplugged") so the isolation proof can prove a bad
  camera degrades to a logged gap without stalling anything.
* :class:`InstrumentFrameSource` -- a real UVC/USB webcam via OpenCV
  ``cv2.VideoCapture`` (the optional ``[monitoring]`` extra); imported lazily
  so the module loads on a base install with no OpenCV.

``read`` returns an ``HxWx3`` ``uint8`` **RGB** frame, or ``None`` when no
frame is available (an unplugged/again-later camera). It must never raise for
an expected hardware condition; the capture service treats ``None`` (and any
escaped exception) as a dropped frame / gap.
"""

from __future__ import annotations

import abc
import sys
import time
from typing import Optional

import numpy as np
from loguru import logger

from PycroFlow.monitoring.config import CameraConfig

# OpenCV capture backends by name. On Windows the default (MSMF) is unreliable
# with many UVC webcams -- it opens but delivers black/empty frames -- so the
# instrument source prefers DirectShow there. `None` means auto-select.
_BACKENDS = {
    "dshow": "CAP_DSHOW",
    "msmf": "CAP_MSMF",
    "v4l2": "CAP_V4L2",
    "avfoundation": "CAP_AVFOUNDATION",
    "any": "CAP_ANY",
}


def _backend_candidates(cv2, backend: Optional[str]) -> list:
    """Ordered (name, cv2-flag) capture backends to try for ``backend``.

    An explicit name pins that one backend; ``None`` auto-selects -- DirectShow
    first on Windows (then MSMF, then ANY), else just ANY.
    """
    if backend:
        name = backend.lower()
        flag = getattr(cv2, _BACKENDS.get(name, "CAP_ANY"), cv2.CAP_ANY)
        return [(name, flag)]
    if sys.platform.startswith("win"):
        return [
            ("dshow", cv2.CAP_DSHOW),
            ("msmf", cv2.CAP_MSMF),
            ("any", cv2.CAP_ANY),
        ]
    return [("any", cv2.CAP_ANY)]


def _quiet_cv2(cv2) -> None:
    """Best-effort: mute OpenCV's stderr backend chatter.

    Auto-selection tries backends in turn, so a backend that can't open a given
    index (e.g. DShow-by-index on some builds) logs a WARN before we fall back
    to one that works. Drop OpenCV's log level to ERROR so that expected
    fallback noise doesn't reach the terminal. No-op if the API differs.
    """
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:  # pragma: no cover - version/build dependent
        pass


def _open_capture(cv2, device, flag):
    """``cv2.VideoCapture(device, flag)`` that never raises.

    Some backends raise a C++ exception on an index they can't handle; treat
    that as "not opened" so auto-selection cleanly moves to the next backend.
    Returns the capture, or ``None`` if construction raised.
    """
    try:
        return cv2.VideoCapture(device, flag)
    except Exception:  # pragma: no cover - needs a specific build/hardware
        return None


# A frame whose mean intensity is at or below this is treated as "black". A UVC
# webcam opened on the wrong index, with no exposure, or behind a privacy
# shutter yields valid-but-empty (all ~0) frames, which pass an "is there a
# frame?" check yet are useless -- the exact symptom of a black monitoring clip.
_BLACK_MEAN = 2.0


def _is_black(frame, threshold: float = _BLACK_MEAN) -> bool:
    """True if ``frame`` is missing, degenerate, or ~uniformly black.

    A missing or zero-size frame counts as black (a size-0 ``mean()`` is NaN,
    which would otherwise slip past the ``<= threshold`` test).
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return True
    return float(frame.mean()) <= threshold


def _apply_camera_settings(cv2, cap, camera) -> None:
    """Apply resolution + optional FOURCC / exposure to an open capture.

    ``fourcc`` (e.g. ``MJPG``), ``auto_exposure`` and ``exposure`` are optional
    per-camera config keys -- some UVC webcams (notably via DirectShow) open but
    deliver black frames until exposure is set manually. Each is a no-op when
    the config leaves it ``None``, so behaviour is unchanged for existing rigs.
    """
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera.height)
    fourcc = getattr(camera, "fourcc", None)
    if fourcc:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*str(fourcc)))
        except Exception:  # pragma: no cover - build/hw specific
            logger.warning("monitoring: FOURCC {!r} not accepted", fourcc)
    auto_exposure = getattr(camera, "auto_exposure", None)
    if auto_exposure is not None:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, float(auto_exposure))
    exposure = getattr(camera, "exposure", None)
    if exposure is not None:
        cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))


class FrameSource(abc.ABC):
    """One camera's frame source."""

    def __init__(self, camera: CameraConfig):
        self.camera = camera
        self.width = camera.width
        self.height = camera.height

    @abc.abstractmethod
    def open(self) -> None:
        """Acquire the device. May raise; the caller marks the camera down."""

    @abc.abstractmethod
    def read(self) -> Optional[np.ndarray]:
        """Return the next ``HxWx3`` RGB uint8 frame, or ``None``."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release the device. Must not raise."""


class EmulatedFrameSource(FrameSource):
    """Synthetic frames: a per-camera tint with a marker that moves each frame.

    The moving marker makes a recorded clip visibly "live" and identifies the
    camera, so the emulator path exercises the same visual review a real clip
    would. Deterministic given the device seed and frame index.

    Parameters
    ----------
    camera : CameraConfig
        Geometry + ``device`` (used as the colour seed).
    fail_mode : str or None
        Fault injection for the isolation proof: ``None`` (healthy),
        ``'slow'`` (sleep ``delay`` s per read), ``'raise'`` (read raises),
        or ``'unplug'`` (open raises / read returns ``None``).
    delay : float
        Seconds slept per read when ``fail_mode == 'slow'``.
    """

    def __init__(
        self,
        camera: CameraConfig,
        *,
        fail_mode: Optional[str] = None,
        delay: float = 0.0,
    ):
        super().__init__(camera)
        self.fail_mode = fail_mode
        self.delay = delay
        self._i = 0
        try:
            self._seed = int(camera.device) % 6
        except (TypeError, ValueError):
            self._seed = abs(hash(str(camera.device))) % 6
        self._base = _tint(self._seed)

    def open(self) -> None:
        if self.fail_mode == "unplug":
            raise OSError("emulated camera unplugged: {}".format(self.camera))

    def read(self) -> Optional[np.ndarray]:
        if self.fail_mode == "slow" and self.delay:
            time.sleep(self.delay)
        if self.fail_mode == "raise":
            raise RuntimeError("emulated camera read failure")
        if self.fail_mode == "unplug":
            return None
        h, w = self.height, self.width
        frame = np.empty((h, w, 3), dtype=np.uint8)
        frame[:] = self._base
        # A marker square sweeping left->right, one step per frame, so the
        # clip is obviously moving and per-frame distinct.
        band = max(4, w // 10)
        x = (self._i * max(1, w // 20)) % max(1, w - band)
        y = h // 3
        frame[y : y + band, x : x + band] = (255, 255, 255)
        self._i += 1
        return frame

    def close(self) -> None:
        pass


class InstrumentFrameSource(FrameSource):
    """A real webcam via OpenCV ``cv2.VideoCapture`` (``[monitoring]`` extra).

    OpenCV is imported lazily in :meth:`open` so importing this module (and the
    whole ``monitoring`` package) works on a base install with no OpenCV; only
    actually starting an instrument capture needs the extra.

    Parameters
    ----------
    backend : str or None
        Capture backend: ``dshow`` / ``msmf`` / ``v4l2`` / ``any``. ``None``
        auto-selects (DirectShow first on Windows, else ANY). DirectShow is
        strongly preferred on Windows -- the default MSMF backend often opens a
        UVC webcam but only yields black frames.
    """

    def __init__(self, camera: CameraConfig, backend: Optional[str] = None):
        super().__init__(camera)
        self._cap = None
        self._backend = backend

    def open(self) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - needs the extra
            raise RuntimeError(
                "instrument capture needs OpenCV; install pip install -e "
                '".[monitoring]"'
            ) from exc
        _quiet_cv2(cv2)
        tried = []
        black_only = None  # first backend that opens + delivers only black
        for name, flag in _backend_candidates(cv2, self._backend):
            cap = _open_capture(cv2, self.camera.device, flag)
            if cap is None or not cap.isOpened():  # pragma: no cover - hw
                if cap is not None:
                    cap.release()
                tried.append(name)
                continue
            _apply_camera_settings(cv2, cap, self.camera)
            # Warm up: MSMF/DShow often return black/empty for the first few
            # reads while the pipeline spins up. Require a real (non-black)
            # frame before committing -- a backend that opens but only yields
            # black (wrong index / unset exposure) is NOT good enough, so fall
            # through to the next backend.
            got, bright = self._warmup(cap)
            if bright:
                logger.info(
                    "monitoring: camera {} opened via {} at {}x{} "
                    "(first frame: yes)",
                    self.camera.role,
                    name,
                    int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width,
                    int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height,
                )
                self._cap = cap
                return
            cap.release()
            tried.append(name + ("(black)" if got else ""))
            if got and black_only is None:
                black_only = (name, flag)
        # No backend delivered a bright frame. Monitoring must never break a run,
        # so if a backend at least delivers (black) frames, commit to it with a
        # loud, actionable warning rather than failing the run outright.
        if black_only is not None:  # pragma: no cover - needs hardware
            name, flag = black_only
            cap = _open_capture(cv2, self.camera.device, flag)
            # The re-open can fail (device grabbed meanwhile / flaky backend);
            # guard it so this "never break a run" branch doesn't itself raise
            # an AttributeError on a None cap. If it fails, fall through to the
            # OSError below -> CaptureThread logs a clean gap.
            if cap is not None and cap.isOpened():
                _apply_camera_settings(cv2, cap, self.camera)
                self._warmup(cap)
                logger.warning(
                    "monitoring: camera {} (device {!r}) opens via {} but "
                    "frames are BLACK (mean~0). Recording proceeds but clips "
                    "will be dark. Likely a wrong device index, an unset "
                    "exposure, or a privacy shutter -- set "
                    "monitoring.cameras[].device / exposure / auto_exposure, "
                    "and run  python -m "
                    "PycroFlow.monitoring.capture_service --probe  to find a "
                    "live (index, backend).",
                    self.camera.role,
                    self.camera.device,
                    name,
                )
                self._cap = cap
                return
            if cap is not None:
                cap.release()
        raise OSError(  # pragma: no cover - needs hardware
            "could not open camera {} (tried backends: {})".format(
                self.camera.device, ", ".join(tried) or "none"
            )
        )

    @staticmethod
    def _warmup(cap, attempts: int = 15):  # pragma: no cover - hw
        """Pull up to ``attempts`` frames; return ``(got_frame, saw_bright)``.

        ``saw_bright`` becomes True once a non-black frame arrives -- many UVC
        cameras deliver a few black frames while the exposure pipeline spins up,
        so a single black frame is not enough to accept (or reject) a backend.
        """
        got = False
        for _ in range(attempts):
            ok, frame = cap.read()
            if ok and frame is not None:
                got = True
                if not _is_black(frame):
                    return True, True
            time.sleep(0.05)
        return got, False

    def read(
        self,
    ) -> Optional[np.ndarray]:  # pragma: no cover - needs hardware
        cap = self._cap
        if cap is None:
            return None
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        import cv2

        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height))
        # OpenCV delivers BGR; the pipeline works in RGB.
        return np.ascontiguousarray(frame[:, :, ::-1])

    def close(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:  # pragma: no cover - needs hardware
            try:
                cap.release()
            except Exception:
                pass


_TINTS = (
    (60, 20, 20),
    (20, 60, 20),
    (20, 20, 60),
    (60, 60, 20),
    (20, 60, 60),
    (60, 20, 60),
)


def _tint(seed: int) -> tuple[int, int, int]:
    return _TINTS[seed % len(_TINTS)]


def make_source(
    camera: CameraConfig,
    mode: str,
    *,
    backend: Optional[str] = None,
    fail_mode: Optional[str] = None,
    delay: float = 0.0,
) -> FrameSource:
    """Build the frame source for ``mode`` (``'emulator'`` / ``'instrument'``).

    ``backend`` selects the OpenCV capture backend for the instrument source;
    ``fail_mode`` / ``delay`` apply only to the emulator source (fault injection
    for tests). Each is ignored by the other source.
    """
    if mode == "instrument":
        return InstrumentFrameSource(camera, backend=backend)
    return EmulatedFrameSource(camera, fail_mode=fail_mode, delay=delay)
