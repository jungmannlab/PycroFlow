"""Pure-Python uncompressed-AVI writer (and a reader for tests).

The monitoring clips must be writable in headless CI with no camera library
installed, and the emulator and instrument capture paths must record
*identically* (only the frame source differs). A dependency-free
uncompressed RIFF/AVI muxer satisfies both: it needs nothing but ``numpy``
(already a base dep), produces a real ``.avi`` playable by VLC / OpenCV /
ffmpeg, and is used by the capture service in both modes.

Frames are 24-bit BGR, stored top-down (negative ``biHeight``) so no vertical
flip is needed. Inputs are ``HxWx3`` ``uint8`` **RGB** arrays (what the
sources and tiler produce); the writer converts to BGR on the way out.

Uncompressed frames are large; the capture service bounds this by recording
only during exchange windows, at a modest fps/resolution, with pool retention.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

import numpy as np

_AVIF_HASINDEX = 0x00000010
_AVIIF_KEYFRAME = 0x00000010
_CHUNK_ID = b"00db"  # stream 0, uncompressed DIB bitmap


class AviWriteError(RuntimeError):
    """Raised when a frame does not match the writer's declared geometry."""


class RawAviWriter:
    """Stream ``HxWx3`` RGB uint8 frames into an uncompressed AVI file.

    Use as a context manager so the header and index are finalised even if the
    caller aborts mid-clip::

        with RawAviWriter(path, width=640, height=480, fps=5) as w:
            for frame in frames:
                w.write(frame)

    A clip with zero frames is still a structurally valid AVI (empty ``movi``
    list + empty index), so a round whose window opened but captured nothing
    yields a real, if empty, file rather than a missing one.

    Parameters
    ----------
    path : str
        Output ``.avi`` path.
    width, height : int
        Frame geometry. Every :meth:`write` frame must match exactly.
    fps : int
        Playback frame rate stored in the stream header.
    """

    def __init__(self, path: str, *, width: int, height: int, fps: int):
        self.path = path
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1, int(fps))
        self._frame_bytes = self.width * self.height * 3
        self._f: BinaryIO = open(path, "wb")
        self._offsets: list[int] = []  # rel. to the 'movi' fourcc
        self._sizes: list[int] = []
        self._n = 0
        self._write_header()

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "RawAviWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing ---------------------------------------------------------
    def write(self, frame: np.ndarray) -> None:
        """Append one ``HxWx3`` RGB uint8 frame."""
        if frame.shape != (self.height, self.width, 3):
            raise AviWriteError(
                "frame shape {} != declared ({}, {}, 3)".format(
                    frame.shape, self.height, self.width
                )
            )
        bgr = np.ascontiguousarray(frame[:, :, ::-1], dtype=np.uint8)
        data = bgr.tobytes()
        offset = self._f.tell() - self._movi_pos  # rel. to 'movi' fourcc
        self._f.write(_CHUNK_ID)
        self._f.write(struct.pack("<I", len(data)))
        self._f.write(data)
        if len(data) & 1:  # pad chunks to an even byte boundary
            self._f.write(b"\x00")
        self._offsets.append(offset)
        self._sizes.append(len(data))
        self._n += 1

    def close(self) -> None:
        """Finalise the ``movi`` list, append the index, patch counts."""
        if self._f.closed:
            return
        f = self._f
        movi_end = f.tell()
        movi_size = movi_end - self._movi_pos  # covers 'movi' + chunks

        idx = bytearray()
        for off, size in zip(self._offsets, self._sizes):
            idx += _CHUNK_ID
            idx += struct.pack("<III", _AVIIF_KEYFRAME, off, size)
        f.write(b"idx1")
        f.write(struct.pack("<I", len(idx)))
        f.write(idx)

        file_end = f.tell()
        _patch(f, self._pos_riff_size, file_end - 8)
        _patch(f, self._pos_movi_size, movi_size)
        _patch(f, self._pos_total_frames, self._n)
        _patch(f, self._pos_stream_length, self._n)
        f.close()

    # -- header ----------------------------------------------------------
    def _write_header(self) -> None:
        """Write the RIFF/hdrl header, recording positions to backpatch.

        Sizes and frame counts are written as zero placeholders and patched in
        :meth:`close`; their file offsets are captured from ``tell()`` as we go
        rather than computed by hand, so the layout can't silently drift.
        """
        f = self._f
        w, h, fps = self.width, self.height, self.fps
        img = self._frame_bytes

        f.write(b"RIFF")
        self._pos_riff_size = f.tell()
        f.write(struct.pack("<I", 0))
        f.write(b"AVI ")

        # LIST hdrl
        f.write(b"LIST")
        pos_hdrl_size = f.tell()
        f.write(struct.pack("<I", 0))
        hdrl_start = f.tell()
        f.write(b"hdrl")

        # avih (MainAVIHeader), 56-byte payload.
        f.write(b"avih")
        f.write(struct.pack("<I", 56))
        f.write(
            struct.pack(
                "<IIII",
                int(1_000_000 / fps),  # dwMicroSecPerFrame
                img * fps,  # dwMaxBytesPerSec
                0,  # dwPaddingGranularity
                _AVIF_HASINDEX,  # dwFlags
            )
        )
        self._pos_total_frames = f.tell()
        f.write(struct.pack("<I", 0))  # dwTotalFrames (patched)
        f.write(
            struct.pack(
                "<IIIIIIIII",
                0,  # dwInitialFrames
                1,  # dwStreams
                img,  # dwSuggestedBufferSize
                w,  # dwWidth
                h,  # dwHeight
                0,
                0,
                0,
                0,  # dwReserved[4]
            )
        )

        # LIST strl
        f.write(b"LIST")
        pos_strl_size = f.tell()
        f.write(struct.pack("<I", 0))
        strl_start = f.tell()
        f.write(b"strl")

        # strh (AVIStreamHeader), 56-byte payload.
        f.write(b"strh")
        f.write(struct.pack("<I", 56))
        f.write(b"vids")
        f.write(b"DIB ")
        f.write(
            struct.pack(
                "<IHHIIII",
                0,  # dwFlags
                0,  # wPriority
                0,  # wLanguage
                0,  # dwInitialFrames
                1,  # dwScale
                fps,  # dwRate  -> fps = dwRate / dwScale
                0,  # dwStart
            )
        )
        self._pos_stream_length = f.tell()
        f.write(struct.pack("<I", 0))  # dwLength (patched)
        f.write(
            struct.pack(
                "<III",
                img,  # dwSuggestedBufferSize
                0,  # dwQuality
                0,  # dwSampleSize
            )
        )
        f.write(struct.pack("<hhhh", 0, 0, w, h))  # rcFrame

        # strf (BITMAPINFOHEADER), 40-byte payload. -h => top-down.
        f.write(b"strf")
        f.write(struct.pack("<I", 40))
        f.write(
            struct.pack(
                "<IiiHHIIiiII",
                40,  # biSize
                w,  # biWidth
                -h,  # biHeight (top-down)
                1,  # biPlanes
                24,  # biBitCount
                0,  # biCompression (BI_RGB)
                img,  # biSizeImage
                0,
                0,  # bi[XY]PelsPerMeter
                0,
                0,  # biClrUsed, biClrImportant
            )
        )

        strl_end = f.tell()
        _patch(f, pos_strl_size, strl_end - strl_start)
        hdrl_end = f.tell()
        _patch(f, pos_hdrl_size, hdrl_end - hdrl_start)

        # LIST movi (streamed into by write()).
        f.write(b"LIST")
        self._pos_movi_size = f.tell()
        f.write(struct.pack("<I", 0))
        self._movi_pos = f.tell()  # position of the 'movi' fourcc
        f.write(b"movi")


def _patch(f: BinaryIO, pos: int, value: int) -> None:
    """Overwrite the little-endian uint32 at ``pos``, then seek back to end."""
    end = f.seek(0, 2)
    f.seek(pos)
    f.write(struct.pack("<I", value))
    f.seek(end)


def read_avi(path: str) -> "list[np.ndarray]":
    """Read an uncompressed AVI written by :class:`RawAviWriter`.

    Walks the RIFF tree to the ``movi`` list and decodes every ``00db``
    chunk back into an ``HxWx3`` RGB uint8 array. Used by the tests to prove
    frames round-trip; not needed at runtime.

    Returns
    -------
    list of numpy.ndarray
        Frames in order (empty list for a zero-frame clip).
    """
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:4] != b"RIFF" or blob[8:12] != b"AVI ":
        raise ValueError("not a RIFF/AVI file: {}".format(path))

    geom: dict[str, int] = {}
    frames: list[np.ndarray] = []

    def _walk(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            fourcc = blob[pos : pos + 4]
            size = struct.unpack("<I", blob[pos + 4 : pos + 8])[0]
            body = pos + 8
            if fourcc == b"LIST":
                list_type = blob[body : body + 4]
                if list_type == b"movi":
                    _read_movi(body + 4, body + size)
                else:
                    _walk(body + 4, body + size)
            elif fourcc == b"strf":
                w, negh = struct.unpack("<ii", blob[body + 4 : body + 12])
                geom["w"], geom["h"] = w, abs(negh)
            pos = body + size + (size & 1)

    def _read_movi(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            fourcc = blob[pos : pos + 4]
            size = struct.unpack("<I", blob[pos + 4 : pos + 8])[0]
            body = pos + 8
            if fourcc == _CHUNK_ID:
                if "w" not in geom:
                    raise ValueError("movi chunk before strf geometry")
                arr = np.frombuffer(
                    blob[body : body + size], dtype=np.uint8
                ).reshape(geom["h"], geom["w"], 3)
                frames.append(np.ascontiguousarray(arr[:, :, ::-1]))
            pos = body + size + (size & 1)

    _walk(12, len(blob))
    return frames


def frame_count(path: str) -> int:
    """Number of decoded frames in ``path`` (convenience for tests)."""
    return len(read_avi(path))
