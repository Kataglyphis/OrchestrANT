"""Integration tests for the dummy ML pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np


if TYPE_CHECKING:
    import pytest

from orchestr_ant_ion.dummy import SimpleMLPreprocessor


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

    def _default_rng() -> _Rng:
        return _Rng()

    monkeypatch.setattr("numpy.random.default_rng", _default_rng)

    ml = SimpleMLPreprocessor(4)
    result = ml.run_pipeline()
    features = cast("np.ndarray", result["features"])
    labels = cast("np.ndarray", result["labels"])
    joke_labels = cast("np.ndarray", result["joke_labels"])

    assert features.shape == (4, 3)
    assert labels.tolist() == [1, 0, 1, 1]
    assert "mean" in result
    assert "std" in result
    assert joke_labels.tolist() == [
        "Definitely ML",
        "Possibly Not",
        "Definitely ML",
        "Definitely ML",
    ]
