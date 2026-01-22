"""Logging helpers for YOLO monitor."""

from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path

from loguru import logger


def configure_logging(
    log_level: str = "DEBUG",
    log_dir: str = "logs",
    *,
    json_logs: bool = False,
) -> None:
    """Configure loguru logging for the monitor."""
    log_level = os.getenv("KATAGLYPHIS_LOG_LEVEL", log_level).upper()
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sink=sys.stdout,
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>"
        ),
        level=log_level,
    )
    log_path = Path(log_dir) / "yolo_{time:YYYY-MM-DD}.log"
    logger.add(
        str(log_path),
        rotation="10 MB",
        retention="7 days",
        level=log_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {process}:{thread} | "
            "{name}:{function}:{line} | {message}"
        ),
    )
    if json_logs:
        json_path = Path(log_dir) / "yolo_{time:YYYY-MM-DD}.jsonl"
        logger.add(
            str(json_path),
            rotation="10 MB",
            retention="7 days",
            level=log_level,
            serialize=True,
        )


def create_log_buffer(max_lines: int = 200) -> deque[str]:
    """Create a bounded log buffer for recent messages."""
    return deque(maxlen=max_lines)


def attach_log_buffer(buffer: deque[str], level: str = "INFO") -> int:
    """Attach a loguru sink that appends messages to a deque."""

    def _sink(message: object) -> None:
        try:
            text = message.rstrip("\n")  # type: ignore[union-attr]
        except Exception:
            text = str(message).rstrip("\n")
        buffer.append(text)

    return logger.add(
        _sink,
        level=level,
        format="{time:HH:mm:ss} | {level: <8} | {message}",
    )
