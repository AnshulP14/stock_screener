"""Logging setup. Call setup_logging() once at startup."""

import logging
import sys

from stock_screening.models.types import LogLevel

PACKAGE_LOGGERS = ["stock_screening", "pydantic_ai", "httpx", "httpcore"]


def setup_logging(level: LogLevel = "INFO", format_string: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        format_string or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))

    for name in PACKAGE_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level))
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False

    if level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
