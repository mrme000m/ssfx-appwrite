"""Structured logging helpers for the PPLX Agent."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import get_settings


def setup_logging(name: str = "pplx_agent", log_dir: Path | None = None) -> logging.Logger:
    """Configure a logger that writes to console and a rotating file."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    target_dir = log_dir or settings.log_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if called multiple times.
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Rotating file
    try:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            target_dir / "pplx_agent.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception as exc:
        logger.warning("Could not create file logger: %s", exc)

    return logger
