from kataglyphispythonpackage.dummy import SimpleMLPreprocessor

import sys
from line_profiler import LineProfiler


def profile_funcs():
    profiler = LineProfiler()
    ml = SimpleMLPreprocessor(10000)

    profiler.add_function(ml.generate_synthetic_data)
    profiler.add_function(ml.normalize_features)
    profiler.add_function(ml.apply_joke_labeling)
    profiler.add_function(ml.run_pipeline)

    # Warm-up
    ml.run_pipeline()

    # Profile each
    profiler.enable()
    ml.generate_synthetic_data()
    ml.normalize_features()
    ml.apply_joke_labeling()
    ml.run_pipeline()
    profiler.disable()

    profiler.print_stats()


if __name__ == "__main__":
    profile_funcs()
