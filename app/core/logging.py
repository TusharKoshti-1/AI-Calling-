"""
app/core/logging.py
Structured logging configuration for the entire app.
Import get_logger() wherever you need a logger.
"""
import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Call once at app startup."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stdout,
    )


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Usage: log = get_logger(__name__)"""
    return logging.getLogger(name)
