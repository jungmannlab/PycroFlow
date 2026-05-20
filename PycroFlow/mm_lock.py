"""Single-process guard for the Micro-Manager Core connection.

Documented invariant: PycroFlow's :class:`PycroFlow.imaging.ImagingSystem`
and a separately-run monet GUI must not both attach to Micro-Manager at the
same time — the second connection silently breaks the first. Until Stage 5
(the in-process Qt GUI that embeds monet) ships, this lockfile-based guard
catches the conflict at startup and refuses to instantiate with a clear
error.

Atomicity comes from ``os.open(O_CREAT | O_EXCL)``. The PID is written
inside the lockfile so a stale lock from a crashed process can be diagnosed
by hand (or, if desired, auto-cleared after verifying the PID is gone).
"""
import errno
import os
import platform
from pathlib import Path


class MmLockHeld(RuntimeError):
    """Raised when the MM Core lock is already held by another process."""


def default_lock_path():
    """Return the canonical lockfile path for this host.

    Windows: ``%LOCALAPPDATA%\\PycroFlow\\mm.lock``
    POSIX:   ``~/.cache/PycroFlow/mm.lock``
    """
    if platform.system() == 'Windows':
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            base = str(Path.home() / 'AppData' / 'Local')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(base) / 'PycroFlow' / 'mm.lock'


class MmCoreLock:
    """Filesystem mutex around the MM Core connection.

    Usage:

        lock = MmCoreLock()
        lock.acquire()   # raises MmLockHeld if already held
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_lock_path()
        self._held = False

    def acquire(self):
        if self._held:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                other_pid = self._read_pid()
                raise MmLockHeld(
                    "Micro-Manager Core lock {} is already held"
                    "{}. Close the other PycroFlow / monet process, or "
                    "delete the file if you are sure no other process is "
                    "using MM.".format(
                        self.path,
                        " (PID {})".format(other_pid) if other_pid else ""
                    )
                )
            raise
        try:
            os.write(fd, str(os.getpid()).encode('utf-8'))
        finally:
            os.close(fd)
        self._held = True

    def release(self):
        if not self._held:
            return
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._held = False

    def _read_pid(self):
        try:
            return self.path.read_text().strip()
        except OSError:
            return None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __del__(self):
        # Best-effort cleanup if user forgot to release.
        try:
            self.release()
        except Exception:
            pass
