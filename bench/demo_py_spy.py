import time
import numpy as np
from loguru import logger

from kataglyphispythonpackage.dummy import SimpleMLPreprocessor


def main():
    preprocessor = SimpleMLPreprocessor(n_samples=1_000)
    result = preprocessor.run_pipeline()

    # Generate two large random matrices (e.g., 10000 x 1000 and 1000 x 10000)
    A = np.random.rand(10000, 1000)
    B = np.random.rand(1000, 10000)

    # Perform matrix multiplication (CPU-intensive)
    C = A @ B  # Shape: (10000, 10000)

    # Apply a computationally expensive element-wise function
    result = np.log(np.exp(C) + 1.0)  # Softplus transformation

    # Optional: Aggregate result to force full evaluation
    final_sum = np.sum(result)
    logger.debug("Computation complete. Sum:", final_sum)


if __name__ == "__main__":
    for i in range(1, 101):         # Run main() 100 times
        logger.debug(f"Run {i}/100 starting.")
        main()
        logger.debug(f"Run {i}/100 finished.\n")
