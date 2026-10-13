"""Logging setup for Itsnrcbot."""

import logging
from logging.handlers import RotatingFileHandler

SHORT_LOG_FORMAT = "[%(asctime)s - %(levelname)s] - %(name)s - %(message)s"

_handlers = [logging.StreamHandler()]
try:  # log file is optional: read-only hosts (some PaaS/containers) must not crash the bot
    _handlers.insert(0, RotatingFileHandler("logs.txt", maxBytes=5 * 1024 * 1024, backupCount=5))
except OSError:
    pass

logging.basicConfig(level=logging.INFO, format=SHORT_LOG_FORMAT, handlers=_handlers)

logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
