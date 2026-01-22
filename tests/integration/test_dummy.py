"""Integration tests for the dummy ML pipeline."""

from __future__ import annotations

import numpy as np
import pytest

from kataglyphispythoninference.dummy import SimpleMLPreprocessor


def test_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate the end-to-end dummy pipeline output."""

    class _Rng:
        def normal(
            self,
            _loc: float,
            _scale: float,
            _size: tuple[int, int],
        ) -> np.ndarray:
            return np.array(
                [
                    [5.5, 5.5, 5.5],
                    [1.0, 1.0, 1.0],
                    [7.0, 5.0, 4.0],
                    [6.0, 4.0, 6.0],
                ]
            )

    monkeypatch.setattr("numpy.random.default_rng", lambda: _Rng())

    ml = SimpleMLPreprocessor(4)
    result = ml.run_pipeline()

    assert result["features"].shape == (4, 3)
    assert result["labels"].tolist() == [1, 0, 1, 1]
    assert "mean" in result
    assert "std" in result
    assert result["joke_labels"].tolist() == [
        "Definitely ML",
        "Possibly Not",
        "Definitely ML",
        "Definitely ML",
    ]
