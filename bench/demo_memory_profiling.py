"""Memory profiler demo for the dummy preprocessing pipeline."""

from memory_profiler import profile

from orchestr_ant_ion.dummy import SimpleMLPreprocessor


@profile
def test_memory_profile() -> None:
    """Run the dummy pipeline under memory profiling."""
    ml = SimpleMLPreprocessor(1000)
    ml.run_pipeline()


test_memory_profile()

