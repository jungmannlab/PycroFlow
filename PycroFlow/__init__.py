"""PycroFlow package entry point.

Importing this package does NOT configure logging or touch the filesystem.
Frontends (CLI, GUI) explicitly call :func:`setup_logging` at startup. Tests
and library users may call it too, or leave logging unconfigured.
"""
from loguru import logger
import os
import sys


_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss:SSS} | PID:{process} | {thread} | "
    "{name} | {function} | {level} -> {message}"
)


_LOGGING_CONFIGURED = False


def log_filter(record):
    """Exclude subpackage logs (pyHamilton, monet) from the main log file."""
    subpackages = ['pyHamilton', 'monet']
    if any(sp in record["name"] for sp in subpackages):
        return False
    return True


def _hamilton_filter(record):
    """Include only pyHamilton subpackage logs (for the hamilton log file)."""
    return "pyHamilton" in record["name"]


def logging_configured():
    """Return whether :func:`setup_logging` has been called this session."""
    return _LOGGING_CONFIGURED


def clean_old_logs(prefix='pycroflow.log', directory='.'):
    """Delete rotated log files matching ``prefix`` in ``directory``.

    Previously called ``rem_old_logfiles`` and run at import time, which
    silently nuked log files in any cwd that imported PycroFlow. Call this
    explicitly when you want the side effect.
    """
    try:
        files = os.listdir(directory)
    except OSError:
        return
    for fil in files:
        if prefix in fil:
            try:
                os.remove(os.path.join(directory, fil))
            except OSError:
                pass


def setup_logging(logfile='pycroflow.log', clean_old=False,
                  stderr_level='ERROR', hamilton_logfile='hamilton.log'):
    """Configure loguru sinks for PycroFlow.

    Three sinks are installed:

    - ``logfile`` (default ``pycroflow.log``): everything except the
      ``pyHamilton`` / ``monet`` subpackages.
    - ``hamilton_logfile`` (default ``hamilton.log``): the verbose
      ``pyHamilton`` serial traffic, kept out of both the terminal and the
      main log. Pass ``hamilton_logfile=None`` to disable it.
    - ``sys.stderr`` at ``stderr_level`` (default ``ERROR``).

    Safe to call multiple times — existing sinks are removed first. Pass
    ``clean_old=True`` to delete any pre-existing rotated log files (the old
    import-time behavior, now opt-in).

    Parameters
    ----------
    logfile : str
        Path for the main rotating log file.
    clean_old : bool
        Delete pre-existing rotated log files for ``logfile`` (and
        ``hamilton_logfile``) before configuring.
    stderr_level : str
        Minimum level shown on the terminal.
    hamilton_logfile : str or None
        Path for the pyHamilton-only log file, or None to skip it.
    """
    global _LOGGING_CONFIGURED
    if clean_old:
        clean_old_logs(prefix=logfile)
        if hamilton_logfile:
            clean_old_logs(prefix=hamilton_logfile)
    logger.remove()
    logger.add(
        logfile,
        format=_LOG_FORMAT,
        filter=log_filter,
        rotation="1 MB",
        retention=5,
        enqueue=True,
        serialize=False,
    )
    if hamilton_logfile:
        logger.add(
            hamilton_logfile,
            format=_LOG_FORMAT,
            filter=_hamilton_filter,
            rotation="1 MB",
            retention=5,
            enqueue=True,
            serialize=False,
        )
    logger.add(sys.stderr, format=_LOG_FORMAT, level=stderr_level)
    _LOGGING_CONFIGURED = True


# Back-compat shims for any caller that imported the old names.
def config_logger():
    """Deprecated: use :func:`setup_logging` instead."""
    setup_logging(clean_old=False)


def rem_old_logfiles():
    """Deprecated: use :func:`clean_old_logs` instead."""
    clean_old_logs()
