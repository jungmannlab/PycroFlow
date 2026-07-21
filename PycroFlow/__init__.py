"""PycroFlow package entry point.

Importing this package does NOT add log sinks or touch the filesystem.
Frontends (CLI, GUI) explicitly call :func:`setup_logging` at startup. Tests
and library users may call it too, or leave logging unconfigured.

The one thing import does do is remove loguru's *default* DEBUG-to-stderr
handler, so code that logs before :func:`setup_logging` runs (e.g. building
a protocol in a startup script) does not flood the terminal. Until
``setup_logging`` installs the real sinks, records simply go nowhere.
"""
from loguru import logger
import os
import sys

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("PycroFlow")
except (ImportError, PackageNotFoundError):  # not installed (e.g. source tree)
    __version__ = "0.0.0"


# loguru auto-installs a DEBUG->stderr handler (id 0) on import. Drop it so
# nothing spams the terminal before a frontend calls setup_logging(); that
# call installs the real sinks (log files + ERROR-level stderr). This adds no
# sinks and writes no files. Want terminal output without the full setup?
# Call setup_logging(stderr_level="INFO") (or "DEBUG").
try:
    logger.remove(0)
except ValueError:
    pass


_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss:SSS} | PID:{process} | {thread} | "
    "{name} | {function} | {level} -> {message}"
)


_LOGGING_CONFIGURED = False
# The arguments the last setup_logging() call used, so redirect_logging() can
# re-install the same sinks in a different directory.
_LOG_CONFIG = {}


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
    _LOG_CONFIG.update(
        logfile=logfile, stderr_level=stderr_level,
        hamilton_logfile=hamilton_logfile)
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


def redirect_logging(directory):
    """Move the log files into ``directory``, keeping the same sink setup.

    Frontends configure logging at startup, before any experiment is loaded,
    so the log files land wherever the app was launched from — typically the
    source checkout. Once the acquisition folder is known, the run's logs
    belong *with the data* they describe, so they can be read (and mined for
    timings) alongside the images.

    Lines written before this call stay in the startup log; the new log
    records where it continued from. No-op unless :func:`setup_logging` has
    been called (a library user's sinks are theirs to manage).

    Parameters
    ----------
    directory : str
        Target folder for the log files. Created if missing.

    Returns
    -------
    str or None
        The absolute path of the new main log file, or None if logging was
        not configured or the redirect failed.
    """
    if not _LOGGING_CONFIGURED:
        return None
    directory = os.path.abspath(directory)
    logfile = os.path.join(directory, os.path.basename(
        _LOG_CONFIG.get('logfile') or 'pycroflow.log'))
    if os.path.abspath(_LOG_CONFIG.get('active_logfile') or '') == logfile:
        return logfile   # already logging there
    hamilton = _LOG_CONFIG.get('hamilton_logfile')
    if hamilton:
        hamilton = os.path.join(directory, os.path.basename(hamilton))
    try:
        os.makedirs(directory, exist_ok=True)
        previous = _LOG_CONFIG.get('active_logfile') or _LOG_CONFIG.get(
            'logfile')
        setup_logging(
            logfile=logfile, clean_old=False,
            stderr_level=_LOG_CONFIG.get('stderr_level', 'ERROR'),
            hamilton_logfile=hamilton)
    except OSError as exc:
        # A bad/unwritable save_dir must not take the logging down with it.
        logger.warning(
            "could not move logs to {}: {!r}; still logging to {}".format(
                directory, exc, _LOG_CONFIG.get('active_logfile')))
        return None
    _LOG_CONFIG['active_logfile'] = logfile
    logger.info("Log continued from {}", previous)
    return logfile


# Back-compat shims for any caller that imported the old names.
def config_logger():
    """Deprecated: use :func:`setup_logging` instead."""
    setup_logging(clean_old=False)


def rem_old_logfiles():
    """Deprecated: use :func:`clean_old_logs` instead."""
    clean_old_logs()
