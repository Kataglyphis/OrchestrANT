"""Pytest-benchmark demo for the dummy preprocessing pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable

from orchestr_ant_ion.dummy import SimpleMLPreprocessor


def test_pipeline_benchmark(
    benchmark: Callable[[Callable[[], object]], object],
) -> None:
    """Benchmark the dummy pipeline execution."""
    ml = SimpleMLPreprocessor(10000)
    benchmark(ml.run_pipeline)

