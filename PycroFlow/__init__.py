import logging
from logging import handlers
import os


# configure logger
def config_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(threadName)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'pycroflow.log', maxBytes=1e6, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)


def rem_old_logfiles():
    files = os.listdir('.')
    files = [fil for fil in files if 'pycroflow.log' in fil]
    for fil in files:
        os.remove(fil)


rem_old_logfiles()  # comment out if old logs are relevant
config_logger()
logger = logging.getLogger(__name__)
