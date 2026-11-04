"""Tests for the embedding benchmark, above all its related-vs-unrelated semantic check."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_embeddings as be  # noqa: E402
from bench_embeddings import GERMAN_TRIPLES, TRIPLES, check_shape, cosine  # noqa: E402


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
        # A broken pooling layer returns these; only this shape check catches them.
        assert any("all zeros" in p for p in check_shape([[0.0, 0.0, 0.0]]))


class TestTripleQuality:
    """A triple whose answer is arguable measures the author's taste."""

    def test_every_triple_has_three_distinct_texts(self):
        for anchor, related, unrelated in TRIPLES:
            assert len({anchor, related, unrelated}) == 3

    def test_there_are_enough_triples_to_mean_something(self):
        assert len(TRIPLES) >= 5

    def test_related_shares_vocabulary_less_than_it_shares_meaning(self):
        # The related text must not repeat the anchor, or this tests overlap, not meaning.
        for anchor, related, _ in TRIPLES:
            assert related.lower() != anchor.lower()
            overlap = len(set(anchor.lower().split()) & set(related.lower().split()))
            assert overlap < len(anchor.split()), (
                f"related text repeats the anchor verbatim: {anchor!r}"
            )


def _words(text):
    return {word.strip(".,:;").lower() for word in text.split()}


class TestGermanTripleQuality:
    """The same bar as English, tighter on shared words: meaning has to do the work."""

    def test_there_are_at_least_as_many_as_english(self):
        assert len(GERMAN_TRIPLES) >= len(TRIPLES)

    def test_every_triple_has_three_distinct_texts(self):
        for triple in GERMAN_TRIPLES:
            assert len(set(triple)) == 3

    def test_related_shares_no_content_word_with_the_anchor(self):
        for anchor, related, _ in GERMAN_TRIPLES:
            shared = {w for w in _words(anchor) & _words(related) if len(w) > 5}
            assert not shared, f"{anchor!r} shares {shared}"

    def test_unrelated_shares_no_content_word_with_the_anchor(self):
        for anchor, _, unrelated in GERMAN_TRIPLES:
            shared = {w for w in _words(anchor) & _words(unrelated) if len(w) > 5}
            assert not shared, f"{anchor!r} shares {shared}"

    def test_no_text_repeats_across_triples(self):
        texts = [text for triple in GERMAN_TRIPLES for text in triple]
        assert len(texts) == len(set(texts))

    def test_they_carry_umlauts_and_sharp_s_so_encoding_is_measured(self):
        joined = " ".join(text for triple in GERMAN_TRIPLES for text in triple)
        assert all(letter in joined for letter in "äöüß")


class _Endpoint:
    """Answers each related text close to its anchor, except the texts it is told to invert."""

    def __init__(self, inverted=()):
        self.inverted = inverted
        self.calls = []

    def __call__(self, base_url, model, texts, timeout=300, entry=None):
        self.calls.append(texts)
        if texts[0] in self.inverted:
            return [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], 0.5
        return [[1.0, 0.0], [1.0, 0.1], [0.0, 1.0]], 0.5


class TestSemanticsReport:
    def test_english_keys_count_only_the_english_triples(self, monkeypatch):
        monkeypatch.setattr(be, "embed", _Endpoint(inverted=[GERMAN_TRIPLES[0][0]]))
        report = {}
        be._measure_semantics("http://x", "m", None, report)
        assert report["semantic_passed"] == report["semantic_total"] == len(TRIPLES)
        assert len(report["margins"]) == len(TRIPLES)

    def test_german_failure_reads_only_in_the_german_section(self, monkeypatch):
        monkeypatch.setattr(be, "embed", _Endpoint(inverted=[GERMAN_TRIPLES[0][0]]))
        report = {}
        be._measure_semantics("http://x", "m", None, report)
        assert report["german"]["passed"] == len(GERMAN_TRIPLES) - 1
        assert report["german"]["total"] == len(GERMAN_TRIPLES)
        assert report["german"]["margins"][0] < 0

    def test_english_failure_leaves_the_german_section_alone(self, monkeypatch):
        monkeypatch.setattr(be, "embed", _Endpoint(inverted=[TRIPLES[0][0]]))
        report = {}
        be._measure_semantics("http://x", "m", None, report)
        assert report["semantic_passed"] == len(TRIPLES) - 1
        assert report["german"]["passed"] == len(GERMAN_TRIPLES)

    def test_the_english_lines_print_as_they_did(self, monkeypatch, capsys):
        monkeypatch.setattr(be, "embed", _Endpoint())
        be._measure_semantics("http://x", "m", None, {})
        out = capsys.readouterr().out
        assert f"    -> semantics {len(TRIPLES)}/{len(TRIPLES)}, median margin" in out
        assert out.index("-> semantics") < out.index("-> German semantics")

    def test_prefixes_reach_anchor_and_passages_only_when_given(self, monkeypatch):
        endpoint = _Endpoint()
        monkeypatch.setattr(be, "embed", endpoint)
        be._measure_semantics("http://x", "m", None, {}, ("query: ", "passage: "))
        anchor, related, unrelated = endpoint.calls[0]
        assert anchor == "query: " + TRIPLES[0][0]
        assert related == "passage: " + TRIPLES[0][1]
        assert unrelated == "passage: " + TRIPLES[0][2]
        endpoint.calls.clear()
        be._measure_semantics("http://x", "m", None, {})
        assert endpoint.calls[0] == list(TRIPLES[0])


class TestThroughput:
    def test_rate_is_texts_over_wall_clock(self, monkeypatch):
        endpoint = _Endpoint()
        monkeypatch.setattr(be, "embed", endpoint)
        result = be._measure_throughput("http://x", "m", None)
        assert result["texts_per_second"] == be.THROUGHPUT_BATCH / 0.5
        assert result["batches"] == be.THROUGHPUT_REPEATS

    def test_every_text_is_distinct_so_no_cache_can_answer(self, monkeypatch):
        endpoint = _Endpoint()
        monkeypatch.setattr(be, "embed", endpoint)
        be._measure_throughput("http://x", "m", None)
        texts = [text for batch in endpoint.calls for text in batch]
        assert len(texts) == len(set(texts))

    def test_a_failing_endpoint_has_no_rate(self, monkeypatch):
        def fail(*_args, **_kwargs):
            raise OSError("down")

        monkeypatch.setattr(be, "embed", fail)
        result = be._measure_throughput("http://x", "m", None)
        assert result["texts_per_second"] is None and result["batches"] == 0


def pytest_approx(value, tol=1e-9):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol

        def __repr__(self):
            return f"~{value}"

    return _Approx()
