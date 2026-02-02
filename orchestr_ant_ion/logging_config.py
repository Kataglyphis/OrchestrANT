"""Logging configuration helpers for the project."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>"
)
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {process}:{thread} | "
    "{name}:{function}:{line} | {message}"
)


def setup_logging(log_filename: str = "logs/catcam.log") -> None:
    """Configure loguru logging for console and rotating file output."""
    log_path = Path(log_filename)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_level = os.getenv("KATAGLYPHIS_LOG_LEVEL", "INFO").upper()
    logger.remove()
    logger.add(
        sink=sys.stdout,
        format=CONSOLE_FORMAT,
        level=log_level,
    )
    logger.add(
        str(log_path),
        rotation="1 MB",
        retention=10,
        compression="zip",
        level=log_level,
        format=FILE_FORMAT,
    )
