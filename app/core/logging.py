"""
app.core.logging
────────────────
Centralised logging setup. Called once at app startup.
"""
from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Initialise root logger with a single stream handler."""
    root = logging.getLogger()
    # Remove any default handlers to avoid duplicate lines
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Tone down noisy third-party loggers
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
