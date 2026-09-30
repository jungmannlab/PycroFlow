"""Archive the raw movie local -> network drive, with a verified checksum.

Design: **ship localizations, keep the movies** — only localizations ever cross
to the cluster; the raw movie is kept (never shipped to the cluster) but, on the
fallback path, MOVED from the acquisition PC's local disk to the network archive
once the live read has finished. The move is gated on the write-target: when
acquisition already writes straight to the pool network folder (the intended
C33 default, B5), there is nothing to move and the step is SKIPPED.

Safety: the local copy is deleted ONLY after the archived copy's checksum is
verified against the source — a failed/partial copy never loses the original.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass

from loguru import logger

# Where the movie was written; drives whether an archive move is needed.
WRITE_TARGET_LOCAL = "local"  # fallback path: local disk -> move to archive
WRITE_TARGET_POOL = "pool"  # intended default: already on the network pool

_CHUNK = 1024 * 1024


@dataclass
class ArchiveResult:
    """Outcome of an archive attempt (for the registry record / logging)."""

    moved: bool
    skipped_reason: str | None
    source: str | None
    dest: str | None
    checksum: str | None
    verified: bool


def _remove_path(path: str) -> None:
    """Remove a file or directory tree if it exists (best-effort)."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except OSError as exc:  # pragma: no cover - FS-timing dependent
        logger.warning("could not remove {}: {!r}".format(path, exc))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_sha256(root: str) -> str:
    """Deterministic checksum over every file in a directory tree."""
    h = hashlib.sha256()
    for dirpath, _dirs, files in sorted(os.walk(root)):
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            h.update(rel.encode("utf-8"))
            h.update(_sha256(p).encode("ascii"))
    return h.hexdigest()


def archive_movie(
    source: str | None,
    archive_dir: str | None,
    *,
    write_target: str = WRITE_TARGET_LOCAL,
) -> ArchiveResult:
    """Move ``source`` (a movie file or its dataset dir) into ``archive_dir``.

    Skips when ``write_target`` is the pool (already on the network), when no
    source/archive is given, or when the source is missing. Otherwise copies to
    the archive, verifies the copy's checksum equals the source's, and only then
    deletes the local original. Never raises on a verification/copy failure — it
    returns ``verified=False`` and leaves the local copy intact so nothing is
    lost; the caller records the outcome.
    """
    if write_target == WRITE_TARGET_POOL:
        return ArchiveResult(
            False, "acquired straight to pool", source, None, None, False
        )
    if not source or not archive_dir:
        return ArchiveResult(
            False, "no source/archive configured", source, None, None, False
        )
    if not os.path.exists(source):
        logger.warning("archive: source does not exist: {}".format(source))
        return ArchiveResult(
            False, "source missing", source, None, None, False
        )

    is_dir = os.path.isdir(source)
    os.makedirs(archive_dir, exist_ok=True)
    dest = os.path.join(archive_dir, os.path.basename(source.rstrip("/")))
    # Copy into a per-process temp dest first, verify THAT, then atomically move
    # it into place. This avoids the stale-partial wedge: copying straight to
    # ``dest`` with ``copytree(dirs_exist_ok=True)`` would merge into leftovers
    # from a prior failed run, so the dest-tree hash would include foreign files
    # and never match the source — a permanent (though safe) checksum mismatch.
    tmp_dest = "{}.partial-{}".format(dest, os.getpid())

    try:
        # Clear any leftover temp from a previously crashed run.
        _remove_path(tmp_dest)
        src_sum = _tree_sha256(source) if is_dir else _sha256(source)
        if is_dir:
            shutil.copytree(source, tmp_dest)
            dst_sum = _tree_sha256(tmp_dest)
        else:
            shutil.copy2(source, tmp_dest)
            dst_sum = _sha256(tmp_dest)
    except Exception as exc:  # noqa: BLE001 - never lose the original
        logger.error(
            "archive copy failed ({!r}); keeping local copy".format(exc)
        )
        _remove_path(tmp_dest)
        return ArchiveResult(False, repr(exc), source, dest, None, False)

    if dst_sum != src_sum:
        logger.error(
            "archive checksum mismatch (src {} != dst {}); keeping local "
            "copy".format(src_sum, dst_sum)
        )
        _remove_path(tmp_dest)
        return ArchiveResult(
            False, "checksum mismatch", source, dest, src_sum, False
        )

    # Verified temp copy — replace any stale final dest, then atomic-rename in.
    try:
        _remove_path(dest)
        os.replace(tmp_dest, dest)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - keep the verified temp + original
        logger.error(
            "archive rename into place failed ({!r}); keeping local copy and "
            "the verified temp {}".format(exc, tmp_dest)
        )
        return ArchiveResult(False, repr(exc), source, dest, src_sum, False)

    # Verified — safe to remove the local original.
    try:
        if is_dir:
            shutil.rmtree(source)
        else:
            os.remove(source)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "archive verified but local delete failed ({!r}); archived copy "
            "is safe".format(exc)
        )
    logger.info("archived raw movie to {} (sha256 {})".format(dest, src_sum))
    return ArchiveResult(True, None, source, dest, src_sum, True)
