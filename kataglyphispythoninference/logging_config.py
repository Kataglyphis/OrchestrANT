import logging
import os

from loguru import logger


def setup_logging(log_filename: str = "logs/catcam.log") -> None:
    log_dir = os.path.dirname(log_filename)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_filename, mode="a"),
        ],
    )
    logger.add(log_filename, rotation="1 MB", retention=10, compression="zip")
