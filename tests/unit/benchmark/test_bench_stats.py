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


def _tools_r3():
    """v070-npu-tools-r3's per-case shape: 42 cases, 3 draws each except one
    case whose other two draws errored; 31 all-pass, 6 all-fail, 5 mixed.
    """
    cases = {f"pass{i}": (3, 3) for i in range(30)}
    cases["one_draw"] = (1, 1)
    cases.update({f"fail{i}": (0, 3) for i in range(6)})
    cases.update({f"once{i}": (1, 3) for i in range(3)})
    cases.update({f"twice{i}": (2, 3) for i in range(2)})
    return cases


class TestClusteredRate:
    """Three draws of one prompt are not three independent trials; counted as
    such, tools-r3's 98/124 printed [71-85 %].
    """

    def test_the_tools_r3_shape_by_hand(self):
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate(_tools_r3())
        assert (c["passes"], c["attempts"], c["n_cases"]) == (98, 124, 42)
        assert c["rate"] == 98 / 124
        # rate = 49/62; residuals passes - rate * attempts, in 62nds:
        # 39 (x30), 13 (x1), -147 (x6), -85 (x3), -23 (x2).
        resid = (30 * 39**2 + 13**2 + 6 * 147**2 + 3 * 85**2 + 2 * 23**2) / 62**2
        var = 42 / 41 * resid / 124**2
        assert c["se"] == pytest.approx(var**0.5)
        assert c["se"] == pytest.approx(0.05861, abs=1e-5)
        # naive = (49/62)(13/62)/124, so the ratio reduces to integers.
        assert c["design_effect"] == pytest.approx(42 * 198186 / (41 * 124 * 637))
        assert c["design_effect"] == pytest.approx(2.570, abs=1e-3)
        assert c["n_eff"] == pytest.approx(124 / c["design_effect"])
        assert (c["low"], c["high"]) == pytest.approx((0.6563, 0.8815), abs=1e-4)

    def test_it_is_wider_than_the_interval_it_replaces(self):
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate(_tools_r3())
        lo, hi = wilson_interval(98, 124)
        assert (round(100 * lo), round(100 * hi)) == (71, 85)
        assert (round(100 * c["low"]), round(100 * c["high"])) == (66, 88)

    def test_one_draw_per_case_costs_only_the_small_sample_factor(self):
        # 0/1 outcomes: sum (y - r)^2 = n r (1 - r), so the design effect is
        # exactly G / (G - 1).
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate({"a": True, "b": False, "c": True, "d": True})
        assert c["design_effect"] == pytest.approx(4 / 3)
        assert c["n_eff"] == pytest.approx(3.0)

    def test_every_attempt_agreeing_takes_the_case_as_the_unit(self):
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate({"a": (3, 3), "b": (3, 3), "c": (3, 3)})
        assert c["design_effect"] is None and c["se"] == 0.0
        assert c["n_eff"] == pytest.approx(3.0)
        assert (c["low"], c["high"]) == pytest.approx(wilson_interval(3, 3))

    def test_one_case_cannot_estimate_the_spread(self):
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate({"only": (2, 3)})
        assert c["se"] is None and c["design_effect"] is None
        assert c["n_eff"] == pytest.approx(1.0)

    def test_a_design_effect_below_one_never_narrows_the_interval(self):
        # Every case at the pooled rate: the clustered variance is zero, and
        # the interval falls back to the unclustered one, not to a point.
        from orchestrant.benchmark.stats import clustered_rate

        c = clustered_rate({"a": (2, 3), "b": (2, 3)})
        assert c["design_effect"] == 0.0 and c["n_eff"] == 6
        assert (c["low"], c["high"]) == pytest.approx(wilson_interval(4, 6))

    def test_the_interval_stays_inside_zero_and_one(self):
        from orchestrant.benchmark.stats import clustered_rate

        cases = {f"c{i}": (0, 3) for i in range(10)}
        cases["x"] = (1, 3)
        c = clustered_rate(cases)
        assert 0.0 <= c["low"] <= c["rate"] <= c["high"] <= 1.0

    def test_unattempted_cases_are_skipped_and_nothing_is_none(self):
        from orchestrant.benchmark.stats import clustered_rate

        assert clustered_rate({}) is None
        assert clustered_rate({"dead": (0, 0)}) is None
        assert clustered_rate({"dead": (0, 0), "x": (1, 1)})["n_cases"] == 1

    def test_rejects_impossible_counts(self):
        from orchestrant.benchmark.stats import clustered_rate

        with pytest.raises(ValueError):
            clustered_rate({"x": (4, 3)})


class TestPairedDifference:
    def test_identical_per_case_outcomes_are_zero_wide(self):
        # Unpaired, the same 98/124 twice printed +/-10 pt around nothing.
        from orchestrant.benchmark.stats import diff_interval, paired_difference

        cases = _tools_r3()
        assert paired_difference(cases, dict(cases)) == (0.0, 0.0, 0.0, 42)
        lo, hi = diff_interval(98, 124, 98, 124)
        assert round(100 * hi) == 10 and lo == -hi

    def test_six_of_27_flipping_by_hand(self):
        # d = -1 on 6 cases, 0 on 21: mean -2/9, sum (d - mean)^2 = 14/3,
        # se = sqrt(14/3 / (27 * 26)).
        from orchestrant.benchmark.stats import paired_difference

        a = {f"c{i}": True for i in range(27)}
        b = {f"c{i}": i >= 6 for i in range(27)}
        mean, lo, hi, n = paired_difference(a, b)
        se = (14 / 3 / (27 * 26)) ** 0.5
        assert n == 27 and mean == pytest.approx(-2 / 9)
        assert (lo, hi) == pytest.approx((-2 / 9 - 1.96 * se, -2 / 9 + 1.96 * se))
        assert (round(100 * lo), round(100 * hi)) == (-38, -6)

    def test_repeats_are_compared_as_per_case_rates(self):
        from orchestrant.benchmark.stats import paired_difference

        a = {"x": (3, 3), "y": (1, 3)}
        b = {"x": (2, 3), "y": (1, 3)}
        assert paired_difference(a, b)[0] == pytest.approx(-1 / 6)

    def test_only_cases_measured_on_both_sides_count(self):
        from orchestrant.benchmark.stats import paired_difference

        a = {"x": (1, 1), "only_a": (1, 1), "dead": (0, 0)}
        b = {"x": (1, 1), "only_b": (0, 1), "dead": (1, 1)}
        assert paired_difference(a, b) == (0.0, -1.0, 1.0, 1)
        assert paired_difference({"a": True}, {"b": True}) is None

    def test_the_interval_is_clamped(self):
        from orchestrant.benchmark.stats import paired_difference

        a = {"x": True, "y": True}
        b = {"x": False, "y": True}
        _, lo, hi, _ = paired_difference(a, b)
        assert lo == -1.0 and hi <= 1.0


class TestPassHatK:
    def test_the_tools_r3_shape_by_hand(self):
        # k=3: the one-draw case cannot count; 2/3 and 1/3 cases score 0.
        from orchestrant.benchmark.stats import pass_hat_k

        assert pass_hat_k(_tools_r3(), 3) == pytest.approx(30 / 41)
        # k=2: a 2/3 case scores C(2,2)/C(3,2) = 1/3; a 1/3 case scores 0.
        assert pass_hat_k(_tools_r3(), 2) == pytest.approx((30 + 2 / 3) / 41)

    def test_it_is_the_unbiased_estimator_not_the_plug_in(self):
        # (2 of 4)^2 would say 25 %; one of the six pairs of draws passes.
        from orchestrant.benchmark.stats import pass_hat_k

        assert pass_hat_k({"x": (2, 4)}, 2) == pytest.approx(1 / 6)

    def test_k_of_one_is_the_mean_per_case_rate(self):
        from orchestrant.benchmark.stats import pass_hat_k

        assert pass_hat_k({"x": (1, 1), "y": (1, 3)}, 1) == pytest.approx(2 / 3)
        assert pass_hat_k({"a": True, "b": False}, 1) == 0.5

    def test_no_case_with_k_attempts_is_none(self):
        from orchestrant.benchmark.stats import pass_hat_k

        assert pass_hat_k({"x": (1, 1)}, 3) is None
        assert pass_hat_k({}, 1) is None
        with pytest.raises(ValueError):
            pass_hat_k({"x": (1, 1)}, 0)


class TestPairedPowerAndMde:
    """'No regression' is only worth the drop the test could have caught."""

    def test_the_rejection_limits_are_the_sign_tests(self):
        from orchestrant.benchmark.stats import _sign_test_rejects, paired_sign_test

        limits = _sign_test_rejects(60, 0.05)
        for d in range(61):
            flagged = [
                d - w
                for w in range(d + 1)
                if w > d - w and paired_sign_test(w, d - w) < 0.05
            ]
            assert limits[d] == max(flagged, default=-1), d

    def test_power_matches_brute_force_enumeration(self):
        import math

        from orchestrant.benchmark.stats import paired_power, paired_sign_test

        n, p_w, p_b = 12, 0.35, 0.05
        brute = sum(
            math.comb(n, w)
            * math.comb(n - w, b)
            * p_w**w
            * p_b**b
            * (1 - p_w - p_b) ** (n - w - b)
            for w in range(n + 1)
            for b in range(n + 1 - w)
            if w > b and paired_sign_test(w, b) < 0.05
        )
        assert paired_power(n, 0.30, 0.05) == pytest.approx(brute, abs=1e-12)

    def test_31_cases_catch_a_ten_point_drop_eight_to_eleven_percent(self):
        from orchestrant.benchmark.stats import paired_power

        assert paired_power(31, 0.10, 0.0) == pytest.approx(0.0834, abs=1e-4)
        assert paired_power(31, 0.10, 0.05) == pytest.approx(0.1066, abs=1e-4)

    def test_six_cases_by_hand(self):
        # With no back-flips, 6 cases regress only if all 6 flip: p^6 = 0.8.
        from orchestrant.benchmark.stats import paired_mde

        assert paired_mde(6, back_flip_rate=0.0) == pytest.approx(
            0.8 ** (1 / 6), abs=1e-4
        )

    def test_five_cases_can_never_regress(self):
        from orchestrant.benchmark.stats import paired_mde

        assert paired_mde(5, back_flip_rate=0.0) is None
        assert paired_mde(0) is None

    def test_the_mde_is_where_power_crosses_eighty_percent(self):
        from orchestrant.benchmark.stats import paired_mde, paired_power

        mde = paired_mde(31)
        assert mde == pytest.approx(0.330, abs=1e-3)
        assert paired_power(31, mde, 0.05) >= 0.8 > paired_power(31, mde - 2e-4, 0.05)

    def test_more_cases_and_fewer_back_flips_detect_less(self):
        from orchestrant.benchmark.stats import paired_mde

        assert paired_mde(124) < paired_mde(42) < paired_mde(31)
        assert paired_mde(31, back_flip_rate=0.0) < paired_mde(31)

    def test_the_note_says_where_the_back_flip_rate_came_from(self):
        from orchestrant.benchmark.stats import paired_mde_note

        assumed = paired_mde_note(31)
        assert "33pt" in assumed and "5%, assumed" in assumed
        observed = paired_mde_note(31, back_flips=2)
        assert "6%, observed" in observed and "assumed" not in observed
        assert "cannot tell" in paired_mde_note(5)


class TestNotes:
    def test_the_clustered_note_only_where_repeats_disagree(self):
        from orchestrant.benchmark.stats import clustered_note

        assert clustered_note(_tools_r3()) == " clustered [66-88%, deff 2.6]"
        assert clustered_note({"a": (3, 3), "b": (0, 3)}) == ""
        assert clustered_note({"a": True, "b": False}) == ""
        # One mixed case: the spread is inestimable, so no design effect.
        assert clustered_note({"a": (2, 3)}) == " clustered [9-97%]"

    def test_the_paired_diff_note(self):
        from orchestrant.benchmark.stats import paired_diff_note

        a = {f"c{i}": True for i in range(27)}
        b = {f"c{i}": i >= 6 for i in range(27)}
        assert paired_diff_note(a, b) == "paired diff -22pt [-38, -6]"
        assert paired_diff_note(a, dict(a)) == "paired diff +0pt [+0, +0]"
        assert paired_diff_note(a, {"other": True}) is None

    def test_the_pass_k_note(self):
        from orchestrant.benchmark.stats import pass_k_note

        note = pass_k_note(_tools_r3(), {"x": (1, 1)}, 3)
        assert note.startswith("pass^3 73% -> n/a (")
