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


def log_filter(record):
    """Exclude subpackage logs (pyHamilton, monet) from the main log file."""
    subpackages = ['pyHamilton', 'monet']
    if any(sp in record["name"] for sp in subpackages):
        return False
    return True


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


def setup_logging(logfile='pycroflow.log', clean_old=False, stderr_level='ERROR'):
    """Configure loguru sinks for PycroFlow.

    Safe to call multiple times — existing sinks are removed first. Pass
    ``clean_old=True`` to delete any pre-existing rotated log files (the old
    import-time behavior, now opt-in).
    """
    if clean_old:
        clean_old_logs(prefix=logfile)
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
    logger.add(sys.stderr, format=_LOG_FORMAT, level=stderr_level)


# Back-compat shims for any caller that imported the old names.
def config_logger():
    """Deprecated: use :func:`setup_logging` instead."""
    setup_logging(clean_old=False)


def rem_old_logfiles():
    """Deprecated: use :func:`clean_old_logs` instead."""
    clean_old_logs()
