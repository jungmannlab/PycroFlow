"""Per-rig camera configuration for the fluidics monitoring subsystem.

The config is a facet of the per-microscope setup YAML: an optional
``monitoring:`` block listing each camera and where clips land. Absent or
empty ``cameras`` => :func:`load_monitoring_config` returns ``None`` and the
subsystem stays inert.

Example ``monitoring:`` block (see ``configs/setups/CameraEmulator.yaml``)::

    monitoring:
      output_dir: /data/fluidics_cam
      fps: 5
      retention_days: 14
      cameras:
        - {role: reservoir, device: 0, width: 640, height: 480}
        - {role: pump,      device: 1, width: 640, height: 480}
        - {role: sample,    device: 2, width: 640, height: 480}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Roles are advisory labels (they name the tile panel / filename); this is
# the recommended vocabulary from the work order, not a hard constraint.
KNOWN_ROLES = ("reservoir", "pump", "sample")


@dataclass(frozen=True)
class CameraConfig:
    """One monitoring camera.

    Parameters
    ----------
    role : str
        What the camera watches (``reservoir`` / ``pump`` / ``sample``).
        Advisory; used to label the tile panel and (for per-camera output)
        the filename.
    device : int or str
        Instrument frame source only: the OpenCV ``VideoCapture`` index or a
        device path/URL. Ignored by the emulator source (which synthesises
        frames), where it doubles as the panel's colour seed.
    width, height : int
        Capture resolution for this camera's panel, in pixels.
    """

    role: str
    device: object = 0
    width: int = 640
    height: int = 480


@dataclass(frozen=True)
class MonitoringConfig:
    """Resolved monitoring configuration for one rig.

    Parameters
    ----------
    cameras : list of CameraConfig
        The cameras to record. Never empty for a live config (an empty list
        makes :func:`load_monitoring_config` return ``None`` instead).
    output_dir : str or None
        Pool/archive directory the per-round clips are written to. ``None``
        (the default when the setup omits it) means "resolve at run start to
        ``<experiment save_dir>/fluidics_cam``" so clips travel with the run's
        other outputs; the controller fills it in. An explicit value overrides
        that. Bulk data stays on the pool, never in git.
    fps : int
        Tile frame rate written to each clip.
    codec : str
        ``raw`` (default) uses the wheel-only pure-Python uncompressed-AVI
        writer, identical in emulator and instrument modes. Reserved for a
        future compressed writer.
    retention_days : int
        Clips older than this (by mtime) in ``output_dir`` are pruned before a
        run. ``0`` disables pruning.
    queue_size : int
        Per-camera bounded frame queue depth inside the capture process
        (drop-oldest under back-pressure).
    source : str or None
        Force the frame source: ``emulator`` (synthetic) or ``instrument``
        (OpenCV). ``None`` lets the caller decide (the controller uses the
        setup's ``emulated`` flag).
    poll_interval : float
        Control-channel poll period in the capture process, seconds.
    """

    cameras: list[CameraConfig]
    output_dir: Optional[str] = None
    fps: int = 5
    codec: str = "raw"
    retention_days: int = 14
    queue_size: int = 2
    source: Optional[str] = None
    poll_interval: float = 0.05
    tile_cols: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialise to a plain dict (written to the child's config file)."""
        return {
            "output_dir": self.output_dir,
            "fps": self.fps,
            "codec": self.codec,
            "retention_days": self.retention_days,
            "queue_size": self.queue_size,
            "source": self.source,
            "poll_interval": self.poll_interval,
            "tile_cols": self.tile_cols,
            "cameras": [
                {
                    "role": c.role,
                    "device": c.device,
                    "width": c.width,
                    "height": c.height,
                }
                for c in self.cameras
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonitoringConfig":
        """Rebuild from :meth:`to_dict` (the capture process's entry point)."""
        cams = [
            CameraConfig(
                role=str(c.get("role", "cam{}".format(i))),
                device=c.get("device", i),
                width=int(c.get("width", 640)),
                height=int(c.get("height", 480)),
            )
            for i, c in enumerate(data.get("cameras", []))
        ]
        out = data.get("output_dir")
        return cls(
            cameras=cams,
            output_dir=str(out) if out else None,
            fps=int(data.get("fps", 5)),
            codec=str(data.get("codec", "raw")),
            retention_days=int(data.get("retention_days", 14)),
            queue_size=int(data.get("queue_size", 2)),
            source=data.get("source"),
            poll_interval=float(data.get("poll_interval", 0.05)),
            tile_cols=data.get("tile_cols"),
        )


def load_monitoring_config(setup: dict) -> Optional[MonitoringConfig]:
    """Extract the ``monitoring:`` block from a loaded setup dict.

    Parameters
    ----------
    setup : dict
        A setup config as returned by :func:`PycroFlow.configs.load_setup`.

    Returns
    -------
    MonitoringConfig or None
        ``None`` when there is no ``monitoring`` block, monitoring is disabled
        (``enabled: false``), or no cameras are declared -- in which case the
        subsystem is inert. Otherwise the resolved config. ``output_dir`` may be
    omitted -- clips then default to ``<experiment save_dir>/fluidics_cam`` at
    run start (the controller resolves it).
    """
    block = (setup or {}).get("monitoring")
    if not block:
        return None
    if block.get("enabled") is False:
        return None
    cams_raw = block.get("cameras") or []
    if not cams_raw:
        return None
    return MonitoringConfig.from_dict(dict(block))
