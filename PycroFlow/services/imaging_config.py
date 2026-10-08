"""Live-analysis configuration seam over an imaging system's ``config``.

The single place that reads the imaging config's ``camera_info`` /
``live_analysis`` blocks and turns the options dict into a
:class:`~PycroFlow.live_analysis.service.FovConfig` — shared by the
orchestrated path (:mod:`PycroFlow.services.live_run`) and the MM-preview
path (:mod:`PycroFlow.services.live_preview`) so the two cannot drift. Both
the real :class:`~PycroFlow.imaging.ImagingSystem` and the emulated one carry
the same ``config`` dict shape (see the setup-YAML ``imaging:`` section).
"""

from __future__ import annotations


def imaging_config(imaging_system) -> dict:
    """The imaging system's config dict ({} when absent/not a dict)."""
    config = getattr(imaging_system, "config", None)
    return config if isinstance(config, dict) else {}


def camera_info_for(imaging_system) -> dict | None:
    """The picasso camera_info the fit needs, or None (live analysis off)."""
    info = imaging_config(imaging_system).get("camera_info")
    return dict(info) if info else None


def live_options_for(imaging_system) -> dict:
    """The optional ``live_analysis`` tuning block ({} when absent)."""
    return dict(imaging_config(imaging_system).get("live_analysis") or {})


def build_fov_config(
    options,
    *,
    camera_info,
    source_kind=None,
    source_kwargs=None,
    source_instance=None,
    batch_size=100,
    use_processes=True,
    **fov_config_fields,
):
    """Build a FovConfig from the shared ``live_analysis`` options block.

    Centralizes the option plumbing (``localize_params`` / ``batch_size`` /
    ``n_workers`` / ``queue_size`` / ``use_processes``) with caller-supplied
    defaults, so a new tunable lands in one place for every live pipeline.

    Parameters
    ----------
    options : dict
        The setup's ``live_analysis`` block (:func:`live_options_for`).
    camera_info : dict
        Full picasso photon-conversion info; its ``Pixelsize`` becomes
        ``pixelsize_nm``.
    source_kind, source_kwargs, source_instance
        The frame source, declarative (kind + kwargs) or pre-built
        (``FovConfig.source_instance``).
    batch_size, use_processes
        Caller defaults, overridden by the options block.
    **fov_config_fields
        Passed through to :class:`FovConfig` (``fov_fields``,
        ``acquisition_fields``, ``archive_dir``, ...).

    Returns
    -------
    FovConfig
    """
    from PycroFlow.live_analysis.service import (
        DEFAULT_LOCALIZE_PARAMS,
        FovConfig,
    )

    if source_kwargs is None:
        source_kwargs = {}
    return FovConfig(
        source_kind=source_kind or "tiff-tail",
        source_kwargs=dict(source_kwargs),
        source_instance=source_instance,
        localize_params=dict(
            options.get("localize_params", DEFAULT_LOCALIZE_PARAMS)
        ),
        batch_size=options.get("batch_size", batch_size),
        n_workers=options.get("n_workers", 2),
        queue_size=options.get("queue_size", 8),
        use_processes=options.get("use_processes", use_processes),
        pixelsize_nm=(camera_info or {}).get("Pixelsize"),
        **fov_config_fields,
    )
