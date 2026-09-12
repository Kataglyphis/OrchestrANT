"""Tests for the interval maths.

The reason this module exists: the suite published "8/12" and "12/12" as though
the second were demonstrably better. It is not, at that sample size — and a
benchmark that hides its own uncertainty is worse than one with no numbers,
because it invites confident wrong conclusions.
"""

import pytest

from orchestrant.benchmark.stats import (
    format_score,
    intervals_overlap,
    significance_note,
    wilson_interval,
)


class TestWilsonInterval:
    def test_certainty_does_not_produce_a_zero_width_interval(self):
        # The normal approximation gives [1.0, 1.0] here, claiming certainty
        # nobody has after 9 observations. Wilson does not.
        lo, hi = wilson_interval(9, 9)
        assert hi == 1.0
        assert lo < 0.9, "an interval this narrow would overstate 9 observations"

    def test_zero_successes_is_handled(self):
        lo, hi = wilson_interval(0, 9)
        assert lo == 0.0 and 0 < hi < 0.5

    def test_more_trials_narrow_the_interval(self):
        small = wilson_interval(8, 12)
        large = wilson_interval(80, 120)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_no_trials_yields_full_range(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_rejects_impossible_input(self):
        with pytest.raises(ValueError):
            wilson_interval(5, 3)

    def test_interval_always_contains_the_point_estimate(self):
        for k, n in ((0, 5), (1, 5), (3, 5), (5, 5), (8, 12), (12, 12)):
            lo, hi = wilson_interval(k, n)
            assert lo <= k / n <= hi, (k, n)


class TestSeparability:
    def test_the_case_that_motivated_this(self):
        # 8/12 vs 12/12 was reported as an improvement. It is not separable.
        assert intervals_overlap(8, 12, 12, 12)

    def test_a_large_clear_difference_is_separable(self):
        assert not intervals_overlap(2, 30, 28, 30)

    def test_identical_scores_overlap(self):
        assert intervals_overlap(5, 10, 5, 10)

    def test_note_names_the_overlap_explicitly(self):
        note = significance_note("new", 12, 12, "old", 8, 12)
        assert "OVERLAP" in note and "not separable" in note.lower()

    def test_note_confirms_a_real_difference(self):
        note = significance_note("A", 30, 30, "B", 5, 30)
        assert "do not overlap" in note

    def test_note_handles_empty_input(self):
        assert "nothing to compare" in significance_note("A", 0, 0, "B", 1, 1)


class TestFormatting:
    def test_score_carries_its_interval(self):
        s = format_score(8, 12)
        assert "8/12" in s and "67%" in s and "[" in s and "]" in s

    def test_no_trials_is_not_rendered_as_zero_percent(self):
        assert format_score(0, 0) == "n/a"


class TestStatisticalPower:
    """'No regression' and 'too small to tell' read identically unless the
    suite says which one it means. These numbers are why the case count is a
    blocker rather than a nice-to-have.
    """

    def test_a_tiny_suite_can_only_prove_a_collapse(self):
        from orchestrant.benchmark.stats import smallest_separable_rate

        mde = smallest_separable_rate(8)
        assert mde is not None and mde <= 0.35, (
            "at n=8 only a near-total collapse is provable"
        )

    def test_more_cases_detect_subtler_drops(self):
        from orchestrant.benchmark.stats import smallest_separable_rate

        assert smallest_separable_rate(60) > smallest_separable_rate(8)

    def test_the_measured_case_needs_about_27(self):
        # Removing the system prompt took a model 100% -> 75%: real, causally
        # understood, and invisible at n=8.
        from orchestrant.benchmark.stats import intervals_overlap

        assert intervals_overlap(8, 8, 6, 8), "n=8 cannot separate it"
        assert not intervals_overlap(27, 27, 20, 27), "n=27 can"

    def test_a_suite_too_small_to_prove_anything_says_so(self):
        from orchestrant.benchmark.stats import power_note, smallest_separable_rate

        assert smallest_separable_rate(2) is None
        assert "cannot prove ANY drop" in power_note(2)

    def test_power_note_states_the_threshold(self):
        from orchestrant.benchmark.stats import power_note

        note = power_note(8)
        assert "n=8" in note and "smallest provable drop" in note

    def test_zero_trials_is_not_a_crash(self):
        from orchestrant.benchmark.stats import smallest_separable_rate

        assert smallest_separable_rate(0) is None


class TestPairedSignTest:
    """Both models answer the SAME cases. Only the cases that disagree carry
    information, and that is a far sharper instrument than two overlapping
    intervals — the roadmap's '119 cases needed' came from the blunt one.
    """

    def test_six_one_way_flips_are_significant(self):
        from orchestrant.benchmark.stats import paired_sign_test

        assert abs(paired_sign_test(6, 0) - 0.03125) < 1e-9

    def test_three_one_way_flips_are_not(self):
        from orchestrant.benchmark.stats import paired_sign_test

        assert abs(paired_sign_test(3, 0) - 0.25) < 1e-9

    def test_no_discordant_cases_is_no_evidence(self):
        from orchestrant.benchmark.stats import paired_sign_test

        assert paired_sign_test(0, 0) == 1.0

    def test_two_sided_and_symmetric(self):
        from orchestrant.benchmark.stats import paired_sign_test

        assert paired_sign_test(8, 1) == paired_sign_test(1, 8)
        assert paired_sign_test(8, 1) < 0.05 < paired_sign_test(7, 1)

    def test_never_exceeds_one(self):
        from orchestrant.benchmark.stats import paired_sign_test

        assert paired_sign_test(4, 4) == 1.0

    def test_rejects_negative_counts(self):
        from orchestrant.benchmark.stats import paired_sign_test

        with pytest.raises(ValueError):
            paired_sign_test(-1, 2)

    def test_the_case_the_overlap_rule_got_wrong(self):
        # 24/27 vs 18/27 with 6-0 discordant cases: the intervals overlap, so
        # the old rule said "not separable". The paired test says p=0.031.
        from orchestrant.benchmark.stats import paired_sign_test

        assert intervals_overlap(24, 27, 18, 27)
        assert paired_sign_test(6, 0) < 0.05


class TestDiffInterval:
    def test_contains_the_point_difference(self):
        from orchestrant.benchmark.stats import diff_interval

        for a, b in ((8, 12), (12, 12), (0, 12), (5, 12)):
            lo, hi = diff_interval(a, 12, b, 12)
            assert lo <= (b - a) / 12 <= hi, (a, b)

    def test_swapping_sides_mirrors_the_interval(self):
        from orchestrant.benchmark.stats import diff_interval

        lo, hi = diff_interval(8, 12, 12, 12)
        lo2, hi2 = diff_interval(12, 12, 8, 12)
        assert abs(lo + hi2) < 1e-12 and abs(hi + lo2) < 1e-12

    def test_is_sharper_than_interval_overlap(self):
        # 8/12 vs 12/12: the two Wilson intervals overlap, yet the interval on
        # the DIFFERENCE excludes zero. Overlap is the more conservative rule.
        from orchestrant.benchmark.stats import diff_interval

        assert intervals_overlap(8, 12, 12, 12)
        lo, _ = diff_interval(8, 12, 12, 12)
        assert lo > 0

    def test_no_trials_yields_the_full_range(self):
        from orchestrant.benchmark.stats import diff_interval

        assert diff_interval(0, 0, 3, 3) == (-1.0, 1.0)

    def test_identical_scores_straddle_zero(self):
        from orchestrant.benchmark.stats import diff_interval

        lo, hi = diff_interval(9, 12, 9, 12)
        assert lo < 0 < hi and abs(lo + hi) < 1e-12


class TestPairedOutcomes:
    def test_counts_bools(self):
        from orchestrant.benchmark.stats import paired_outcomes

        a = {"x": True, "y": False, "z": True, "w": False}
        b = {"x": False, "y": True, "z": True, "w": False}
        assert paired_outcomes(a, b) == (1, 1, 2)

    def test_counts_repeat_pairs_by_rate(self):
        from orchestrant.benchmark.stats import paired_outcomes

        a = {"x": (3, 3), "y": (1, 3)}
        b = {"x": (2, 3), "y": (1, 3)}
        assert paired_outcomes(a, b) == (1, 0, 1)

    def test_only_shared_cases_count_and_unmeasured_ones_are_skipped(self):
        from orchestrant.benchmark.stats import paired_outcomes

        a = {"x": (1, 1), "only_a": (1, 1), "dead": (0, 0)}
        b = {"x": (1, 1), "only_b": (0, 1), "dead": (1, 1)}
        assert paired_outcomes(a, b) == (0, 0, 1)


class TestTiers:
    """A ranking that orders strictly by point estimate prints an ordering the
    data may not support. Adjacent rows the paired test cannot separate belong
    in one tier.
    """

    @staticmethod
    def _row(name, fails):
        return {"label": name, "cases": {f"c{i}": i >= fails for i in range(27)}}

    def test_six_flips_start_a_new_tier(self):
        from orchestrant.benchmark.stats import tiers

        rows = [self._row("a", 0), self._row("b", 6)]
        assert [
            [r["label"] for r in t] for t in tiers(rows, key=lambda r: r["cases"])
        ] == [["a"], ["b"]]

    def test_three_flips_share_a_tier(self):
        from orchestrant.benchmark.stats import tiers

        rows = [self._row("a", 0), self._row("b", 3)]
        assert [
            [r["label"] for r in t] for t in tiers(rows, key=lambda r: r["cases"])
        ] == [["a", "b"]]

    def test_tiers_chain_through_adjacent_rows(self):
        # a~b and b~c are each within noise; a and c may not be, but the rows
        # are ADJACENT-compared, so all three share a tier. Documented choice.
        from orchestrant.benchmark.stats import tiers

        rows = [self._row("a", 0), self._row("b", 4), self._row("c", 8)]
        assert len(tiers(rows, key=lambda r: r["cases"])) == 1

    def test_empty_ranking(self):
        from orchestrant.benchmark.stats import tiers

        assert tiers([], key=lambda r: r) == []


class TestPairedPower:
    def test_six_flips_is_the_floor_at_five_percent(self):
        from orchestrant.benchmark.stats import smallest_detectable_flips

        assert smallest_detectable_flips() == 6

    def test_note_states_the_floor(self):
        from orchestrant.benchmark.stats import paired_power_note

        assert "6 cases" in paired_power_note()
