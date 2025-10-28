# import logging
# from logging import handlers
from loguru import logger
import os
import sys


# configure logger
def config_logger():
    # logger = logging.getLogger(__name__)
    # logger.setLevel(logging.DEBUG)
    # formatter = logging.Formatter(
    #     '%(asctime)s | %(threadName)s | %(name)s | %(levelname)s -> %(message)s')
    # file_handler = handlers.RotatingFileHandler(
    #     'pycroflow.log', maxBytes=1e6, backupCount=5)
    # file_handler.setFormatter(formatter)
    # file_handler.setLevel(logging.DEBUG)
    # logger.addHandler(file_handler)

    # using loguru
    logfile = "pycroflow.log"
    logger.remove()
    logger.add(
        logfile,
        format="{time:YYYY-MM-DD HH:mm:ss:SSS} | PID:{process} | {thread} | {name} | {function} | {level} -> {message}",
        filter=log_filter,
        rotation="1 MB", retention=5, enqueue=True, serialize=False)
    logger.add(
        sys.stderr,
        format="{time:YYYY-MM-DD HH:mm:ss:SSS} | PID:{process} | {thread} | {name} | {function} | {level} -> {message}",
        level="ERROR")


def log_filter(record):
    subpackages = ['pyHamilton', 'monet']
    if any([sp in record["name"] for sp in subpackages]):
        return False
    return True


def rem_old_logfiles():
    files = os.listdir('.')
    files = [fil for fil in files if 'pycroflow.log' in fil]
    for fil in files:
        os.remove(fil)


rem_old_logfiles()  # comment out if old logs are relevant
config_logger()
# logger = logging.getLogger(__name__)
