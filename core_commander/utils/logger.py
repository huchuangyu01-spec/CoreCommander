# -*- coding: utf-8 -*-
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name="core_commander"):
    """
    Sets up a structured logger writing to console and a rotating file.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers if logger is already configured
    if logger.handlers:
        return logger

    # Log format with timestamp, level, thread name and message
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(threadName)s] [%(filename)s:%(lineno)d] - %(message)s'
    )

    # Console Handler (check for None to prevent crashes in pythonw/noconsole mode)
    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File Handler - rotating log up to 5MB, keeping 3 backups
    log_dir = os.path.join(os.path.expanduser("~"), ".core_commander")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "core_commander.log")
        file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # Fallback if AppData log dir cannot be created
        fallback_log = "core_commander.log"
        try:
            file_handler = RotatingFileHandler(fallback_log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:  # nosec
            pass

    return logger

# Globally available logger instance
logger = setup_logger()
