import numpy as np
import pytest

from kataglyphispythonpackage.dummy import SimpleMLPreprocessor


def test_generate_synthetic_data():
    ml = SimpleMLPreprocessor(1000)
    features, labels = ml.generate_synthetic_data()
    assert features.shape == (1000, 3)
    assert labels.shape == (1000,)
    assert set(np.unique(labels)).issubset({0, 1})


def test_normalize_features():
    ml = SimpleMLPreprocessor(10)
    ml.features = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    normalized = ml.normalize_features()
    assert normalized.shape == (3, 3)
    assert np.allclose(normalized.mean(axis=0), 0, atol=1e-7)


def test_apply_joke_labeling():
    ml = SimpleMLPreprocessor(5)
    ml.labels = np.array([1, 0, 1, 0, 1])
    jokes = ml.apply_joke_labeling()
    expected = np.array(
        [
            "Definitely ML",
            "Possibly Not",
            "Definitely ML",
            "Possibly Not",
            "Definitely ML",
        ]
    )
    assert np.array_equal(jokes, expected)
