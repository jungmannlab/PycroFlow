"""Generate the PycroFlow line-art logo.

A single continuous line in the Picasso one-line style: a flowing liquid
stroke (the automated fluid exchange PycroFlow orchestrates) rising from a
baseline and curling into a droplet. Sibling of the Picasso DNA-helix
line-art and the monet attenuation-curve line.

Writes two SVGs:

* ``pycroflow-logo-square.svg`` -- the motif only, 200x200, the source the
  app-icon pipeline (see Picasso's ``make_picasso_icon.py``) rasterises.
* ``pycroflow-logo.svg`` -- a horizontal lockup whose flow line sweeps on
  under the ``PycroFlow`` wordmark, echoing the Picasso signature card.

Usage, from this folder::

    python make_pycroflow_logo.py            # both SVGs
    python make_pycroflow_logo.py --render   # also PNG previews
"""

from __future__ import annotations

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STROKE = "#231f20"
STROKE_WIDTH = 1.8
WORDMARK = "PycroFlow"

_APP = None


def _qt_app():
    """Return a kept-alive QGuiApplication (Qt needs it for fonts/SVG)."""
    global _APP
    from PyQt6 import QtGui

    _APP = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(
        sys.argv[:1]
    )
    return _APP


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _catmull_rom(points: np.ndarray, samples: int = 40) -> np.ndarray:
    """Smooth (clamped) Catmull-Rom spline through ``points`` (N, 2)."""
    pts = np.asarray(points, float)
    ext = np.vstack([pts[0], pts, pts[-1]])
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        t = np.linspace(0, 1, samples, endpoint=False)[:, None]
        t2, t3 = t * t, t * t * t
        out.append(
            0.5
            * (
                (2 * p1)
                + (-p0 + p2) * t
                + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
            )
        )
    out.append(pts[-1][None])
    return np.vstack(out)


def _teardrop(t: np.ndarray, cx, cy, w, h, m=2.0) -> np.ndarray:
    """Teardrop outline for angles ``t``; negative ``h`` puts the cusp up."""
    u = np.cos(t)
    v = np.sin(t) * np.sin(t / 2.0) ** m
    v = v / np.max(np.abs(v))
    return np.column_stack([cx + v * (w / 2.0), cy + u * (h / 2.0)])


def _droplet(cx, cy, w, h, t0_deg=210.0, overlap=1.012) -> np.ndarray:
    """One open droplet loop (cusp up), entered at its lower-left."""
    t0 = np.deg2rad(t0_deg)
    t = t0 + np.linspace(0, 2 * np.pi * overlap, 440)
    return _teardrop(t, cx, cy, w, -h, m=2.0)


# --------------------------------------------------------------------------- #
# the two compositions
# --------------------------------------------------------------------------- #
def build_square() -> np.ndarray:
    """Motif centreline for the square icon: drop upper-right, flow to it."""
    drop = _droplet(128.0, 82.0, 74.0, 98.0)
    join = drop[0]
    tang = drop[0] - drop[1]
    tang = tang / np.linalg.norm(tang)
    guide = np.array(
        [
            [26.0, 176.0],
            [50.0, 171.0],
            [72.0, 174.0],
            [92.0, 162.0],
            [112.0, 152.0],
            join + tang * 14.0,
            join,
        ]
    )
    return np.vstack([_catmull_rom(guide), drop])


def build_lockup_line(right: float) -> np.ndarray:
    """Centreline for the wordmark lockup: a small drop at the left whose
    flow line sweeps on to ``right`` as a baseline under the wordmark."""
    # drop base sits on the flow baseline so entry/exit stay nearly level
    drop = _droplet(64.0, 110.0, 56.0, 80.0, t0_deg=214.0)
    join = drop[0]
    tin = drop[0] - drop[1]
    tin = tin / np.linalg.norm(tin)
    # a short curl in from the left, meeting the drop tangentially
    lead = np.array(
        [
            [26.0, 156.0],
            [40.0, 158.0],
            join + tin * 12.0,
            join,
        ]
    )
    # the line exits the drop base and flows right as a long, gently
    # undulating baseline running just under the lettering
    exit_pt = drop[-1]
    tout = drop[-1] - drop[-6]
    tout = tout / np.linalg.norm(tout)
    tail = np.array(
        [
            exit_pt,
            exit_pt + tout * 12.0,
            [0.30 * right, 166.0],
            [0.52 * right, 159.0],
            [0.74 * right, 165.0],
            [right, 160.0],
        ]
    )
    return np.vstack(
        [_catmull_rom(lead), drop, _catmull_rom(tail, samples=60)]
    )


# --------------------------------------------------------------------------- #
# SVG emission
# --------------------------------------------------------------------------- #
def _polyline_d(pts: np.ndarray) -> str:
    d = [f"M{pts[0, 0]:.2f},{pts[0, 1]:.2f}"]
    d += [f"L{x:.2f},{y:.2f}" for x, y in pts[1:]]
    return "".join(d)


_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg id="Layer_1" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" \
viewBox="{vb}">
  <defs>
    <style>
      .cls-1 {{
        fill: none;
        stroke: {stroke};
        stroke-linecap: round;
        stroke-linejoin: round;
        stroke-miterlimit: 10;
        stroke-width: {width}px;
      }}
      .cls-2 {{ fill: {stroke}; stroke: none; }}
    </style>
  </defs>
{comment}
  <path class="cls-1" d="{line}"/>{text}
</svg>
"""

_SQUARE_COMMENT = (
    "  <!-- PycroFlow: a single continuous line = the automated fluid flow\n"
    "       it orchestrates, rising from a baseline and curling into a\n"
    "       droplet. Sibling of the Picasso DNA-helix and monet power-curve\n"
    "       line-art. -->"
)
_LOCKUP_COMMENT = (
    "  <!-- PycroFlow wordmark lockup: the droplet's flow line sweeps on\n"
    "       under the name, echoing the Picasso signature card. -->"
)


def _wordmark_path_d(baseline_x, baseline_y, cap_height):
    """Outline WORDMARK as an SVG path, baseline left-anchored at the
    given point, scaled to ``cap_height`` (via Qt system fonts)."""
    from PyQt6 import QtCore, QtGui

    _qt_app()
    # a fine serif harmonises with the hairline line-art (convert to
    # outlines so the SVG carries no font dependency)
    font = QtGui.QFont("DejaVu Serif", 100)
    path = QtGui.QPainterPath()
    path.addText(QtCore.QPointF(0, 0), font, WORDMARK)

    # scale so the capital height matches cap_height
    caps = QtGui.QPainterPath()
    caps.addText(QtCore.QPointF(0, 0), font, "P")
    scale = cap_height / caps.boundingRect().height()

    cmds = []
    i, n = 0, path.elementCount()

    def pt(e):
        return (baseline_x + e.x * scale, baseline_y + e.y * scale)

    while i < n:
        e = path.elementAt(i)
        if e.type == QtGui.QPainterPath.ElementType.MoveToElement:
            cmds.append("M{:.2f},{:.2f}".format(*pt(e)))
            i += 1
        elif e.type == QtGui.QPainterPath.ElementType.LineToElement:
            cmds.append("L{:.2f},{:.2f}".format(*pt(e)))
            i += 1
        elif e.type == QtGui.QPainterPath.ElementType.CurveToElement:
            c1 = pt(e)
            c2 = pt(path.elementAt(i + 1))
            end = pt(path.elementAt(i + 2))
            cmds.append(
                "C{:.2f},{:.2f} {:.2f},{:.2f} {:.2f},{:.2f}".format(
                    *c1, *c2, *end
                )
            )
            i += 3
        else:
            i += 1
    cmds.append("Z")
    width = path.boundingRect().width() * scale
    return "".join(cmds), width


def write_square():
    pts = build_square()
    svg = _SVG.format(
        vb="0 0 200 200",
        stroke=STROKE,
        width=STROKE_WIDTH,
        comment=_SQUARE_COMMENT,
        line=_polyline_d(pts),
        text="",
    )
    with open("pycroflow-logo-square.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote pycroflow-logo-square.svg")
    return svg


def write_lockup():
    base_x, base_y, cap = 150.0, 120.0, 58.0
    text_d, word_w = _wordmark_path_d(base_x, base_y, cap)
    right = base_x + word_w
    pts = build_lockup_line(right)
    vb_w = right + 24.0
    text = '\n  <path class="cls-2" d="{}"/>'.format(text_d)
    svg = _SVG.format(
        vb=f"0 0 {vb_w:.0f} 200",
        stroke=STROKE,
        width=STROKE_WIDTH,
        comment=_LOCKUP_COMMENT,
        line=_polyline_d(pts),
        text=text,
    )
    with open("pycroflow-logo.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote pycroflow-logo.svg")
    return svg, vb_w


def _render(svg: str, out: str, w: int, h: int):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtCore, QtGui, QtSvg

    _qt_app()
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    img = QtGui.QImage(w, h, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor("white"))
    p = QtGui.QPainter(img)
    r.render(p)
    p.end()
    img.save(out)
    print(f"wrote {out}")


# --------------------------------------------------------------------------- #
# app icon (pycroflow.ico): the motif on a white rounded tile so it stays
# visible on dark docks/taskbars -- same treatment as make_picasso_icon.py
# --------------------------------------------------------------------------- #
_TILE = 824 / 1024
_RADIUS = 0.2237
_FILL = 0.62
_STROKE_BOOST = 1.6
_MIN_STROKE = 1.4
_ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _with_stroke(svg, width):
    import re

    return re.sub(
        r"(stroke-width:\s*)([\d.]+)",
        lambda m: f"{m.group(1)}{width:.4g}",
        svg,
    )


def _alpha(image):
    from PyQt6 import QtGui  # noqa: F401

    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    return (
        rows[:, : 4 * image.width()]
        .reshape(image.height(), image.width(), 4)[..., 3]
        .copy()
    )


def _content_bounds(svg):
    from PyQt6 import QtCore, QtGui, QtSvg

    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    view = r.viewBoxF()
    res = 2048 / max(view.width(), view.height())
    img = QtGui.QImage(
        round(view.width() * res),
        round(view.height() * res),
        QtGui.QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(img)
    QtSvg.QSvgRenderer(
        QtCore.QByteArray(_with_stroke(svg, 2 / res).encode())
    ).render(p)
    p.end()
    ys, xs = np.nonzero(_alpha(img))
    return QtCore.QRectF(
        view.x() + xs.min() / res,
        view.y() + ys.min() / res,
        (xs.max() - xs.min()) / res,
        (ys.max() - ys.min()) / res,
    )


def _icon_png(svg, bounds, stroke_src, size):
    from PyQt6 import QtCore, QtGui, QtSvg

    tile = _TILE * size
    scale = _FILL * tile / max(bounds.width(), bounds.height())
    stroke = max(stroke_src * scale * _STROKE_BOOST, _MIN_STROKE) / scale
    r = QtSvg.QSvgRenderer(
        QtCore.QByteArray(_with_stroke(svg, stroke).encode())
    )
    half = size / 2 / scale
    c = bounds.center()
    r.setViewBox(QtCore.QRectF(c.x() - half, c.y() - half, 2 * half, 2 * half))

    icon = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    icon.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(icon)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    off = (size - tile) / 2
    path = QtGui.QPainterPath()
    path.addRoundedRect(
        QtCore.QRectF(off, off, tile, tile), _RADIUS * tile, _RADIUS * tile
    )
    p.fillPath(path, QtCore.Qt.GlobalColor.white)
    r.render(p)
    p.end()
    return icon


def write_icon(svg):
    from io import BytesIO

    from PIL import Image

    _qt_app()
    stroke_src = STROKE_WIDTH
    bounds = _content_bounds(svg)
    imgs = []
    for s in _ICO_SIZES:
        qimg = _icon_png(svg, bounds, stroke_src, s)
        buf = QByteArrayIO(qimg)
        imgs.append(Image.open(BytesIO(buf)).convert("RGBA"))
    # The canonical icon (release/logos, matching Picasso) and the copy that
    # ships inside the package (PycroFlow/gui/) so the installed/frozen GUI can
    # load it as its window icon. Writing both keeps them from drifting.
    targets = ["pycroflow.ico"]
    pkg_copy = os.path.join(
        os.path.dirname(__file__), "..", "..", "PycroFlow", "gui"
    )
    if os.path.isdir(pkg_copy):
        targets.append(os.path.join(pkg_copy, "pycroflow.ico"))
    for target in targets:
        imgs[-1].save(
            target,
            format="ICO",
            sizes=[(s, s) for s in _ICO_SIZES],
            append_images=imgs[:-1],
        )
        print("wrote", os.path.normpath(target))


def QByteArrayIO(qimg):
    """Encode a QImage as PNG bytes."""
    from PyQt6 import QtCore

    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    qimg.save(buf, "PNG")
    return bytes(ba)


def main(argv):
    square = write_square()
    lockup, vb_w = write_lockup()
    if "--render" in argv:
        _render(square, "pycroflow-logo-square.png", 512, 512)
        _render(lockup, "pycroflow-logo.png", round(vb_w * 2), 400)
    if "--icon" in argv:
        write_icon(square)


if __name__ == "__main__":
    main(sys.argv[1:])
