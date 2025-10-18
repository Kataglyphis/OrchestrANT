from memory_profiler import profile
from kataglyphispythonpackage.dummy import SimpleMLPreprocessor


@profile
def test_memory_profile():
    ml = SimpleMLPreprocessor(1000)
    ml.run_pipeline()


test_memory_profile()
