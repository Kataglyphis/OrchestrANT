# test_fib.py
from kataglyphispythoninference.dummy import SimpleMLPreprocessor


def test_pipeline_benchmark(benchmark):
    ml = SimpleMLPreprocessor(10000)
    benchmark(ml.run_pipeline)
