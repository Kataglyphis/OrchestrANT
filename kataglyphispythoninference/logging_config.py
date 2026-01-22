import os

from loguru import logger


def setup_logging(log_filename: str = "logs/catcam.log") -> None:
    log_dir = os.path.dirname(log_filename)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    log_level = os.getenv("KATAGLYPHIS_LOG_LEVEL", "INFO").upper()
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>"
        ),
        level=log_level,
    )
    logger.add(
        log_filename,
        rotation="1 MB",
        retention=10,
        compression="zip",
        level=log_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {process}:{thread} | "
            "{name}:{function}:{line} | {message}"
        ),
    )
