"""timeit demo for the dummy preprocessing pipeline."""

import timeit

from loguru import logger

from orchestr_ant_ion.dummy import SimpleMLPreprocessor


def run() -> None:
    """Run the dummy pipeline for timing."""
    ml = SimpleMLPreprocessor(10000)
    ml.run_pipeline()


if __name__ == "__main__":
    duration = timeit.timeit("run()", setup="from __main__ import run", number=5)
    avg = duration / 5
    logger.info("Average runtime over 5 runs: %.4f seconds", avg)

