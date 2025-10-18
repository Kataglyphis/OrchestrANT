import numpy as np
import pytest

from kataglyphispythonpackage.dummy import SimpleMLPreprocessor


def test_pipeline(monkeypatch):
    monkeypatch.setattr(
        "numpy.random.normal",
        lambda loc, scale, size: np.array(
            [
                [5.5, 5.5, 5.5],
                [1.0, 1.0, 1.0],
                [7.0, 5.0, 4.0],
                [6.0, 4.0, 6.0],
            ]
        ),
    )

    ml = SimpleMLPreprocessor(4)
    result = ml.run_pipeline()

    assert result["features"].shape == (4, 3)
    assert result["labels"].tolist() == [1, 0, 1, 1]
    assert "mean" in result and "std" in result
    assert result["joke_labels"].tolist() == [
        "Definitely ML",
        "Possibly Not",
        "Definitely ML",
        "Definitely ML",
    ]
