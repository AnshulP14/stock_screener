"""
Logging configuration for stock_screening.

Usage:
    from stock_screening.logging_config import setup_logging, get_logger
    
    setup_logging()  # Call once at startup
    logger = get_logger(__name__)
"""

import logging
import sys

from stock_screening.models.types import LogLevel

# Package loggers to configure
PACKAGE_LOGGERS = [
    "stock_screening",
    "pydantic_ai",
    "httpx",
    "httpcore",
]


def setup_logging(
    level: LogLevel = "INFO",
    format_string: str | None = None,
) -> None:
    """
    Configure logging for the application.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        format_string: Custom format string, or None for default
    """
    if format_string is None:
        format_string = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    
    # Configure root handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(format_string, datefmt="%H:%M:%S"))
    
    # Set up package loggers
    for logger_name in PACKAGE_LOGGERS:
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, level))
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False
    
    # Reduce noise from httpx/httpcore unless DEBUG
    if level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name."""
    return logging.getLogger(name)
