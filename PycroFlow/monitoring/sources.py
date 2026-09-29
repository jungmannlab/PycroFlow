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
        tried = []
        for name, flag in _backend_candidates(cv2, self._backend):
            cap = cv2.VideoCapture(self.camera.device, flag)
            if not cap.isOpened():  # pragma: no cover - needs hardware
                cap.release()
                tried.append(name)
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            # Warm up: MSMF/DShow often return black/empty for the first few
            # reads while the pipeline spins up. Pull a few and check we get a
            # real frame before committing to this backend.
            got = self._warmup(cap)
            logger.info(
                "monitoring: camera {} opened via {} at {}x{} "
                "(first frame: {})",
                self.camera.role,
                name,
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width,
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height,
                "yes" if got else "NONE -- check camera not in use / privacy",
            )
            self._cap = cap
            return
        raise OSError(  # pragma: no cover - needs hardware
            "could not open camera {} (tried backends: {})".format(
                self.camera.device, ", ".join(tried) or "none"
            )
        )

    @staticmethod
    def _warmup(cap, attempts: int = 10) -> bool:  # pragma: no cover - hw
        for _ in range(attempts):
            ok, frame = cap.read()
            if ok and frame is not None:
                return True
            time.sleep(0.05)
        return False

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
