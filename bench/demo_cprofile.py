"""cProfile demo for the dummy preprocessing pipeline."""

import cProfile
import pstats

from kataglyphispythoninference.dummy import SimpleMLPreprocessor


def main() -> None:
    """Run the dummy pipeline for profiling."""
    ml = SimpleMLPreprocessor(10000)
    ml.run_pipeline()


if __name__ == "__main__":
    profiler = cProfile.Profile()
    profiler.enable()
    main()
    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats(pstats.SortKey.CUMULATIVE).print_stats(20)
