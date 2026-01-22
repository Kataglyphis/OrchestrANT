from __future__ import annotations

from loguru import logger


def configure_logging(log_level: str = "DEBUG") -> None:
    """Configure loguru logging for the monitor."""

    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{message}</cyan>",
        level=log_level,
    )
    logger.add(
        "detection_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="7 days",
        level=log_level,
    )
