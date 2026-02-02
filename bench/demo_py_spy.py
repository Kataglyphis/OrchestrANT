"""py-spy demo workload for CPU profiling."""

import numpy as np
from loguru import logger

from orchestr_ant_ion.dummy import SimpleMLPreprocessor


def main() -> None:
    """Run a CPU-intensive workload for profiling."""
    preprocessor = SimpleMLPreprocessor(n_samples=1_000)
    _result = preprocessor.run_pipeline()

    rng = np.random.default_rng()

    # Generate two large random matrices (e.g., 10000 x 1000 and 1000 x 10000)
    a = rng.random((10000, 1000))
    b = rng.random((1000, 10000))

    # Perform matrix multiplication (CPU-intensive)
    c = a @ b  # Shape: (10000, 10000)

    # Apply a computationally expensive element-wise function
    result = np.log(np.exp(c) + 1.0)  # Softplus transformation

    # Optional: Aggregate result to force full evaluation
    final_sum = np.sum(result)
    logger.debug("Computation complete. Sum: %s", final_sum)


if __name__ == "__main__":
    for i in range(1, 101):  # Run main() 100 times
        logger.debug("Run %s/100 starting.", i)
        main()
        logger.debug("Run %s/100 finished.\n", i)

