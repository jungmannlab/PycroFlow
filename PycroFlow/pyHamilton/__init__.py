from .psd import *
from .communication import *
from .util import *
from .command import *
from .commandPSD4 import *
from .commandPSD4SmoothFlow import *
from .commandPSD6 import *
from .commandPSD6SmoothFlow import *

from loguru import logger
import os
import sys


def log_filter(record):
    return "pyHamilton" in record["name"]


def clean_old_logs(prefix='pyhamilton.log', directory='.'):
    """Delete rotated pyHamilton log files. Opt-in; no longer runs at import."""
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


def setup_logging(logfile='hamilton.log', clean_old=False):
    """Add a pyHamilton-only file sink. Safe to call multiple times."""
    if clean_old:
        clean_old_logs(prefix=logfile)
    logger.add(
        logfile,
        format="{time:YYYY-MM-DD HH:mm:ss:SSS} | PID:{process} | {thread} | {name} | {function} | {level} -> {message}",
        filter=log_filter,
        rotation="1 MB",
        retention=5,
        enqueue=True,
        serialize=False,
    )


# Back-compat shims for the old names.
def config_logger():
    """Deprecated: use :func:`setup_logging` instead."""
    setup_logging(clean_old=False)


def rem_old_logfiles():
    """Deprecated: use :func:`clean_old_logs` instead."""
    clean_old_logs()


#List of pumps. Initially the list is empty
pumps = []
pumpLength = 16


def connect(port, baudrate):
    initializeSerial(port, baudrate)

def disconnect():
    disconnectSerial()

def executeCommand(pump, command, waitForPump=False):
    if pump.checkValidity(command):
        sendCommand(pump.asciiAddress, command, waitForPump)

def definePump(address: str, type: util.PSDTypes, syringe: util.SyringeTypes):
    if len(pumps) < pumpLength:
        newPump = PSD(address, type)
        logging.debug("Enable h Factor Commands and Queries")
        sendCommand(newPump.asciiAddress, newPump.command.enableHFactorCommandsAndQueries() + newPump.command.executeCommandBuffer())
        result = sendCommand(newPump.asciiAddress, newPump.command.syringeModeQuery(), True)
        resolution = result[3:4]
        newPump.setResolution(int(resolution))
        newPump.calculateSteps()
        newPump.calculateSyringeStroke()
        newPump.setVolume(syringe)
        pumps.append(newPump)