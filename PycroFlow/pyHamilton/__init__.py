from .psd import *
from .communication import *
from .util import *
from .command import *
from .commandPSD4 import *
from .commandPSD4SmoothFlow import *
from .commandPSD6 import *
from .commandPSD6SmoothFlow import *

import logging
from logging import handlers
import os


# configure logger
def config_logger():
    logger = logging.getLogger('pyHamilton')
    for handler in logger.handlers:  # don't log into the main pycroflow.log file
        logger.removeHandler(handler)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(threadName)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'pyhamilton.log', maxBytes=1e6, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    # logger.addHandler(stream_handler)


def rem_old_logfiles():
    files = os.listdir('.')
    files = [fil for fil in files if 'pyhamilton.log' in fil]
    for fil in files:
        os.remove(fil)


rem_old_logfiles()  # comment out if old logs are relevant
config_logger()
logger = logging.getLogger('pyHamilton')


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