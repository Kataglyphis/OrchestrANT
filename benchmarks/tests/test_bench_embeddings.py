"""Tests for the embedding benchmark.

The point of this benchmark is the semantic check, so that is what the tests
concentrate on: an endpoint can return well-formed vectors of the right
dimension at a fine rate and still be useless, and only the related-vs-unrelated
comparison catches it.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_embeddings import TRIPLES, check_shape, cosine  # noqa: E402


class TestCosine:
    def test_identical_vectors_are_one(self):
        assert cosine([1, 2, 3], [1, 2, 3]) == pytest_approx(1.0)

    def test_orthogonal_vectors_are_zero(self):
        assert cosine([1, 0], [0, 1]) == pytest_approx(0.0)

    def test_opposite_vectors_are_minus_one(self):
        assert cosine([1, 0], [-1, 0]) == pytest_approx(-1.0)

    def test_scale_does_not_matter(self):
        assert cosine([1, 2], [2, 4]) == pytest_approx(1.0)

    def test_a_zero_vector_does_not_divide_by_zero(self):
        assert cosine([0, 0], [1, 1]) == 0.0


class TestShapeChecks:
    def test_well_formed_vectors_have_no_problems(self):
        assert check_shape([[0.1, 0.2], [0.3, 0.4]]) == []

    def test_no_vectors_is_reported(self):
        assert check_shape([]) == ["no vectors returned"]

    def test_inconsistent_dimensions_are_caught(self):
        assert any("inconsistent" in p for p in check_shape([[1.0], [1.0, 2.0]]))

    def test_non_finite_values_are_caught(self):
        assert any("non-finite" in p for p in check_shape([[float("nan"), 1.0]]))
        assert any("non-finite" in p for p in check_shape([[float("inf"), 1.0]]))

    def test_an_all_zero_vector_is_caught(self):
        # A broken pooling layer returns these, and every shape check but this
        # one passes them.
        assert any("all zeros" in p for p in check_shape([[0.0, 0.0, 0.0]]))


class TestTripleQuality:
    """A triple whose answer is arguable measures the author's taste."""

    def test_every_triple_has_three_distinct_texts(self):
        for anchor, related, unrelated in TRIPLES:
            assert len({anchor, related, unrelated}) == 3

    def test_there_are_enough_triples_to_mean_something(self):
        assert len(TRIPLES) >= 5

    def test_related_shares_vocabulary_less_than_it_shares_meaning(self):
        # The related text must not simply repeat the anchor: that would test
        # string overlap, which any embedding passes, rather than meaning.
        for anchor, related, _ in TRIPLES:
            assert related.lower() != anchor.lower()
            overlap = len(set(anchor.lower().split()) & set(related.lower().split()))
            assert overlap < len(anchor.split()), (
                f"related text repeats the anchor verbatim: {anchor!r}"
            )


def pytest_approx(value, tol=1e-9):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol

        def __repr__(self):
            return f"~{value}"

    return _Approx()
