"""Burn a subtitle onto a monitor frame: the run-sequence step + how long.

A tiny, dependency-light overlay (Pillow, which rides along with matplotlib —
always present) shared by the two monitor recordings: the webcam capture
subprocess composites it onto the RGB tile, and the fluorescence "fluid step
monitor" acquisition burns it onto its grayscale frames. Both show the current
Run-Sequence step and its elapsed time, like movie subtitles, so a recording
can be read back against the protocol.

Only monitor recordings are annotated — scientific acquisitions are never
touched (the caller gates on the ``monitoring`` flag).
"""

from __future__ import annotations


def format_elapsed(seconds) -> str:
    """Format an elapsed duration as ``m:ss`` (or ``h:mm:ss`` past an hour)."""
    s = max(0, int(seconds))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return "{:d}:{:02d}:{:02d}".format(h, m, s)
    return "{:d}:{:02d}".format(m, s)


def subtitle_text(label, elapsed_s) -> str:
    """The subtitle string: ``"<step label>  ·  <m:ss>"`` (or just the time)."""
    label = (label or "").strip()
    elapsed = format_elapsed(elapsed_s)
    return "{}  ·  {}".format(label, elapsed) if label else elapsed


def _font(size):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no size arg on the default font
        return ImageFont.load_default()


def _text_size(draw, text, font):
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    except Exception:  # pragma: no cover - very old Pillow
        return int(draw.textlength(text, font=font)), 11


def _font_size_for(height, font_size):
    if font_size is not None:
        return int(font_size)
    return max(11, int(height * 0.04))


def burn_subtitle_rgb(frame, text, *, font_size=None, pad=6):
    """Return ``frame`` (HxWx3 uint8 RGB) with a bottom subtitle bar + text.

    A new array is returned; the input is left untouched. An empty ``text`` or a
    frame too small for the bar returns the frame unchanged.
    """
    import numpy as np

    if not text:
        return frame
    arr = np.ascontiguousarray(frame, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3:
        return frame
    from PIL import Image, ImageDraw

    h, w = arr.shape[:2]
    img = Image.fromarray(arr).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    font = _font(_font_size_for(h, font_size))
    _tw, th = _text_size(draw, text, font)
    bar_h = th + 2 * pad
    if bar_h >= h:
        return frame
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 160))
    draw.text(
        (pad, h - bar_h + pad), text, fill=(255, 255, 255, 255), font=font
    )
    return np.asarray(img, dtype=np.uint8)


def burn_subtitle_gray(frame, text, *, font_size=None, pad=6):
    """Return a 2-D grayscale ``frame`` (uint8/uint16) with a subtitle burned in.

    The bottom strip is set dark and the text drawn in the array's max value. A
    new array is returned; an empty ``text``, a non-2-D frame, or a frame too
    small for the bar returns the input unchanged.
    """
    import numpy as np

    if not text:
        return frame
    arr = np.asarray(frame)
    if arr.ndim != 2:
        return frame
    from PIL import Image, ImageDraw

    h, w = arr.shape
    font = _font(_font_size_for(h, font_size))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    _tw, th = _text_size(probe, text, font)
    bar_h = th + 2 * pad
    if bar_h >= h:
        return frame
    mask_img = Image.new("L", (w, bar_h), 0)
    ImageDraw.Draw(mask_img).text((pad, pad), text, fill=255, font=font)
    mask = np.asarray(mask_img)

    out = np.array(arr, copy=True)
    if np.issubdtype(out.dtype, np.integer):
        hi = np.iinfo(out.dtype).max
    else:
        hi = float(out.max()) or 1.0
    y0 = h - bar_h
    strip = out[y0:h]
    strip[:] = 0  # dark bar behind the text
    strip[mask > 127] = hi  # bright text pixels
    out[y0:h] = strip
    return out
