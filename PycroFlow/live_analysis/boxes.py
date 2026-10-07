"""Picasso-style detection boxes for live thumbnails.

The single implementation of the identify-for-overlay step every thumbnail
pusher uses (the ``--demo``/``--live`` launchers and the Live tab's MM
preview): run picasso's ``identify_in_image`` on one frame and return the
spot centres in the transport-friendly flat ``[x0, y0, x1, y1, ...]`` shape
the Overview draws (see :func:`PycroFlow.live_analysis.client_seam.push_thumbnail`).
Kept service-side so the GUI stays picasso-free — it only renders the
coordinates it is handed.
"""

from __future__ import annotations


def identify_boxes(frame, box_size, min_net_gradient):
    """Detect spot centres on one frame for the box overlay. Never raises.

    Parameters
    ----------
    frame : numpy.ndarray
        One 2-D frame.
    box_size : int
        Picasso's odd box size (``Box Size`` localize parameter).
    min_net_gradient : float
        Picasso's detection threshold (``Min. Net Gradient``).

    Returns
    -------
    list of float or None
        Flat ``[x0, y0, x1, y1, ...]`` centres (possibly empty), or None when
        detection is unavailable/failed — the overlay is best-effort.
    """
    try:
        import numpy as np
        from picasso.localize import identify_in_image

        # picasso's own identify_in_frame casts first ("otherwise numba goes
        # crazy"): calling identify_in_image with a raw uint16 camera frame
        # makes the numba gradient kernel WRAP AROUND on unsigned arithmetic
        # — background pixels get astronomical "net gradients", flooding the
        # overlay with boxes that no threshold can suppress.
        ys, xs, _ng = identify_in_image(
            np.float32(frame), float(min_net_gradient), int(box_size)
        )
        out: list[float] = []
        for xi, yi in zip(xs, ys):
            out.append(float(xi))
            out.append(float(yi))
        return out
    except Exception:  # noqa: BLE001 - the overlay is best-effort
        return None
