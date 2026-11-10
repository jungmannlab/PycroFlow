"""Render accumulated live-analysis localizations to a super-resolution PNG.

At the end of a FOV the live pipeline already holds every localization it fit
for that field in memory (:class:`~PycroFlow.live_analysis.metrics.RunningMetrics`).
This module turns that table into a picasso-style super-resolution image and
writes it next to the raw movie, so the operator has a quick visual of the FOV
to eyeball in retrospect without re-rendering offline.

Best-effort and self-contained: the one picasso-render call lives here behind a
small, mockable surface, and every failure is the caller's to swallow (the
service wraps it so a render problem never breaks acquisition).
"""

from __future__ import annotations


def _merge_info(info, pixelsize_nm, locs):
    """Build the picasso ``info`` dict render needs: Width/Height/Pixelsize.

    ``info`` may be a dict or picasso's list-of-dicts. Missing Width/Height are
    derived from the localization extent; the pixel size falls back to
    ``pixelsize_nm``. Returns None when the pixel size is unknown — picasso's
    ``field_of_view`` needs it to size the render and raises otherwise.
    """
    import numpy as np

    merged: dict = {}
    if isinstance(info, dict):
        merged.update(info)
    elif isinstance(info, (list, tuple)):
        for inf in info:
            if isinstance(inf, dict):
                merged.update(inf)
    px = merged.get("Pixelsize") or pixelsize_nm
    if not px:
        return None
    merged["Pixelsize"] = float(px)
    if not merged.get("Width") or not merged.get("Height"):
        merged["Width"] = int(np.ceil(float(locs["x"].max()))) + 1
        merged["Height"] = int(np.ceil(float(locs["y"].max()))) + 1
    return merged


def _safe_oversampling(requested, field_edge, max_edge):
    """Clamp oversampling so the rendered edge stays within ``max_edge`` px.

    A large camera at high oversampling would allocate a huge float image; this
    keeps the render bounded (e.g. a 2048 px camera caps at 2x for a 4096 edge).
    """
    ov = max(1, int(requested or 1))
    if field_edge and max_edge:
        while ov > 1 and field_edge * ov > max_edge:
            ov -= 1
    return ov


def save_render_png(
    locs,
    out_path,
    *,
    info=None,
    pixelsize_nm=None,
    oversampling=8,
    max_edge=4096,
    vmax_percentile=99.9,
    cmap="hot",
):
    """Render ``locs`` to a super-resolution PNG at ``out_path``.

    Parameters
    ----------
    locs : pandas.DataFrame
        The accumulated localizations (picasso schema: ``x`` / ``y`` ...).
    out_path : str
        Destination PNG path.
    info : dict or list of dict or None
        picasso metadata; supplies Width/Height/Pixelsize when present.
    pixelsize_nm : float or None
        Camera pixel size (nm) — used when ``info`` carries no ``Pixelsize``.
    oversampling : int
        Display pixels per camera pixel (clamped by ``max_edge``).
    max_edge : int
        Upper bound on the rendered image's longest edge (pixels).
    vmax_percentile : float
        Display-stretch upper percentile.
    cmap : str
        Matplotlib colormap name.

    Returns
    -------
    str or None
        The written path, or None when there is nothing to render (no
        localizations, unknown pixel size, or an empty image).
    """
    import numpy as np

    if locs is None or len(locs) == 0:
        return None
    merged = _merge_info(info, pixelsize_nm, locs)
    if merged is None:
        return None
    field_edge = max(int(merged["Width"]), int(merged["Height"]))
    ov = _safe_oversampling(oversampling, field_edge, max_edge)

    from picasso import render as picasso_render

    # picasso 1.0: ``disp_px_size`` is the DISPLAY pixel size in nm, so
    # pixelsize_nm / oversampling yields ``oversampling`` display px per camera
    # px (field_of_view = Width * Pixelsize / disp_px_size = Width * ov).
    _n, image = picasso_render.render(
        locs,
        merged,
        disp_px_size=float(merged["Pixelsize"]) / ov,
        blur_method="smooth",
        min_blur_width=1.0,
    )
    img = np.asarray(image, dtype="float32")
    if img.size == 0:
        return None
    vmax = float(np.percentile(img, vmax_percentile))
    if vmax <= 0:
        vmax = float(img.max())
    if vmax <= 0:
        vmax = 1.0

    from matplotlib import image as mpimage

    mpimage.imsave(
        out_path, img, cmap=cmap, vmin=0.0, vmax=vmax, origin="upper"
    )
    return out_path
