import logging
logging.basicConfig(level=logging.WARNING)

def get_my_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    return logger