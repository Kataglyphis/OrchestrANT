import pstats
import cProfile

from kataglyphispythonpackage.dummy import SimpleMLPreprocessor


def main():
    ml = SimpleMLPreprocessor(10000)
    ml.run_pipeline()


if __name__ == "__main__":
    profiler = cProfile.Profile()
    profiler.enable()
    main()
    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats(pstats.SortKey.CUMULATIVE).print_stats(20)
