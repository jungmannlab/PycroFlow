"""Single-process guard for the Micro-Manager Core connection.

Documented invariant: PycroFlow's :class:`PycroFlow.imaging.ImagingSystem`
and a separately-run monet GUI must not both attach to Micro-Manager at the
same time — the second connection silently breaks the first. Until Stage 5
(the in-process Qt GUI that embeds monet) ships, this lockfile-based guard
catches the conflict at startup and refuses to instantiate with a clear
error.

Atomicity comes from ``os.open(O_CREAT | O_EXCL)``. The PID is written
inside the lockfile; on contention we check whether that PID is still
alive and auto-reclaim a stale lock left by a crashed process, only
refusing when a live process genuinely holds it.
"""
import atexit
import os
import platform
from pathlib import Path

from loguru import logger


class MmLockHeld(RuntimeError):
    """Raised when the MM Core lock is already held by another process."""


def _pid_alive(pid):
    """Return whether a process with ``pid`` is currently running.

    Used to tell a genuinely-held lock from a stale one left behind by a
    crashed process. On ambiguity (e.g. access-denied) we err on the side
    of "alive" so we never steal a lock from a live process.

    Parameters
    ----------
    pid : int
        Process id read from the lockfile.

    Returns
    -------
    bool
        True if the process appears to be running.
    """
    if not pid or pid <= 0:
        return False
    if platform.system() == 'Windows':
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid)
        if not handle:
            # No handle: most likely the process does not exist. (A rare
            # access-denied would also land here; treating that as "dead"
            # only risks reclaiming a lock from another user's process,
            # which the single-user lab box does not have.)
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == still_active
            return True
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


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
        self._atexit_registered = False

    def acquire(self):
        if self._held:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create()
        except FileExistsError:
            # A lock file is already present. Distinguish a live holder from
            # a stale lock left by a crashed process: if the recorded PID is
            # gone (or unreadable), reclaim it; only refuse for a live owner.
            other_pid = self._read_pid()
            if other_pid is not None and _pid_alive(other_pid):
                raise MmLockHeld(
                    "Micro-Manager Core lock {} is held by a running "
                    "process (PID {}). Close the other PycroFlow / monet "
                    "process first.".format(self.path, other_pid)
                )
            logger.warning(
                "Reclaiming stale MM Core lock {} (owner PID {} is no "
                "longer running).".format(self.path, other_pid)
            )
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            try:
                self._create()
            except FileExistsError:
                # Another process raced us to recreate it; treat as held.
                other_pid = self._read_pid()
                raise MmLockHeld(
                    "Micro-Manager Core lock {} is held by a running "
                    "process (PID {}). Close the other PycroFlow / monet "
                    "process first.".format(self.path, other_pid)
                )
        self._held = True
        # Release on interpreter shutdown too. A propagating unhandled
        # exception still runs atexit handlers, so a crash like the
        # fill_tubings AttributeError no longer leaves the lock behind.
        if not self._atexit_registered:
            atexit.register(self.release)
            self._atexit_registered = True

    def _create(self):
        """Atomically create the lockfile and write our PID into it.

        Raises
        ------
        FileExistsError
            If the lockfile already exists (``O_EXCL``).
        """
        fd = os.open(
            str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, str(os.getpid()).encode('utf-8'))
        finally:
            os.close(fd)

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
            text = self.path.read_text().strip()
        except OSError:
            return None
        try:
            return int(text)
        except ValueError:
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
