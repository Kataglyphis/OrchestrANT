"""bench_compare's statistics: case-clustered intervals, the paired
difference, the minimum detectable drop and pass^k.

Split from test_bench_compare.py, which is frozen at its size. The measured
anchor is v070-npu-tools-r3: 42 cases x 3 draws, 31 all-pass, 6 all-fail and
5 mixed, whose 98/124 printed [71-85 %] where by case it is [66-88 %].
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_compare as bcmp  # noqa: E402
from bench_compare import compare, normalise  # noqa: E402


def draws_report(cases, deterministic=False, label="m", errored=None):
    """A bench_tools report with one row per draw: {case: [bool, ...]}; the
    `errored` cases each gain two errored draws, as tools-r3's one case did."""
    rows = [
        {"case": name, "attempt": i, "passed": ok}
        for name, outcomes in cases.items()
        for i, ok in enumerate(outcomes)
    ]
    rows += [
        {"case": name, "attempt": i, "passed": False, "errored": True}
        for name in errored or ()
        for i in (1, 2)
    ]
    passed = sum(ok for outcomes in cases.values() for ok in outcomes)
    total = sum(len(outcomes) for outcomes in cases.values())
    # A deterministic lane's unit is the case, as the producers count it.
    case_k = sum(all(outcomes) for outcomes in cases.values())
    return {
        "benchmark": "bench_tools",
        "provenance": {},
        "config": {"repeats": max(len(v) for v in cases.values())},
        "reports": [
            {
                "label": label,
                "model": label,
                "passed": passed,
                "total": total,
                "effective_n": len(cases) if deterministic else total,
                "effective_k": case_k if deterministic else passed,
                "deterministic": deterministic,
                "results": rows,
            }
        ],
    }


def tools_r3(**kw):
    cases = {f"pass{i}": [True] * 3 for i in range(30)}
    cases["one_draw"] = [True]
    cases.update({f"fail{i}": [False] * 3 for i in range(6)})
    cases.update({f"once{i}": [True, False, False] for i in range(3)})
    cases.update({f"twice{i}": [True, True, False] for i in range(2)})
    return normalise(draws_report(cases, errored=["one_draw"], **kw))


def score_line(findings):
    return next(f for f in findings if " -> " in f and "/" in f)


class TestClusteredIntervalBesideTheScore:
    def test_mixed_repeats_print_the_case_clustered_interval(self):
        findings, _ = compare(tools_r3(), tools_r3())
        assert "98/124 = 79% [71-85%] clustered [66-88%, deff 2.6]" in score_line(
            findings
        )

    def test_repeats_that_agree_keep_todays_display(self):
        cases = {"a": [True] * 3, "b": [False] * 3}
        r = normalise(draws_report(cases))
        line = score_line(compare(r, r)[0])
        assert "3/6 = 50%" in line and "clustered" not in line

    def test_single_draws_keep_todays_display(self):
        r = normalise(draws_report({"a": [True], "b": [False], "c": [True]}))
        assert "clustered" not in score_line(compare(r, r)[0])

    def test_the_interval_covers_the_cases_behind_the_score(self):
        # A candidate's score leaves the suspect cases; the control's does not.
        entry = {"cases": {"ok": (2, 3), "bad": (0, 3)}, "total": 3}
        assert bcmp._scored_cases(entry, {"bad"}) == {"ok": (2, 3)}
        control = {"cases": {"ok": (3, 3), "bad": (0, 3)}, "total": 6}
        assert bcmp._scored_cases(control, {"bad"}) == control["cases"]


class TestPairedDifferenceInterval:
    def test_identical_per_case_outcomes_are_zero_wide(self):
        # Newcombe printed [-10, +10] around two byte-identical runs.
        findings, _ = compare(tools_r3(), tools_r3())
        assert "paired diff +0pt [+0, +0]" in score_line(findings)

    def test_a_paired_drop_is_estimated_per_case(self):
        a = {f"c{i}": [True] for i in range(27)}
        b = {f"c{i}": [i >= 6] for i in range(27)}
        findings, _ = compare(normalise(draws_report(a)), normalise(draws_report(b)))
        assert "paired diff -22pt [-38, -6]" in score_line(findings)

    def test_reports_without_cases_keep_the_unpaired_interval(self):
        def bare(passed):
            return normalise(
                {
                    "benchmark": "bench_tools",
                    "reports": [{"label": "m", "passed": passed, "total": 27}],
                }
            )

        line = score_line(compare(bare(27), bare(21))[0])
        assert "   diff -22pt [-41, -5]" in line and "paired diff" not in line


class TestMinimumDetectableDrop:
    MDE_42 = "minimum detectable drop at 80% power: 26pt (~11 of 42 paired cases"

    def test_unchanged_is_followed_by_the_drop_it_could_have_missed(self):
        findings, _ = compare(tools_r3(), tools_r3())
        i = next(i for i, f in enumerate(findings) if "unchanged" in f)
        assert findings[i + 1].startswith("  m: " + self.MDE_42)
        assert "back-flip rate 5%, assumed" in findings[i + 1]

    def test_an_observed_back_flip_rate_is_used(self):
        # 3 broke and 3 fixed of 30: unchanged, and 10 % flip back.
        a = {f"c{i}": [not 3 <= i < 6] for i in range(30)}
        b = {f"c{i}": [i >= 3] for i in range(30)}
        findings, _ = compare(normalise(draws_report(a)), normalise(draws_report(b)))
        assert any("unchanged" in f and "3 worse / 3 better" in f for f in findings)
        assert any("back-flip rate 10%, observed" in f for f in findings)

    def test_the_closing_verdict_states_it(self, capsys):
        new = tools_r3()
        seen = {}
        _, regressed = compare(tools_r3(), new, seen=seen)
        assert bcmp._verdict(new, regressed, seen) == 0
        out = capsys.readouterr().out
        assert out.index("no regression detected") < out.index(self.MDE_42)

    def test_the_directory_verdict_states_it(self, tmp_path, capsys):
        for name in ("old", "new"):
            (tmp_path / name).mkdir()
            cases = {f"c{i}": [True] for i in range(31)}
            (tmp_path / name / "r.json").write_text(json.dumps(draws_report(cases)))
        args = argparse.Namespace(
            reports=[str(tmp_path / "old"), str(tmp_path / "new")],
            time_tolerance=0.25,
        )
        assert bcmp._compare_directories(args) == 0
        out = capsys.readouterr().out
        assert "no regression detected\n    minimum detectable drop" in out
        assert "33pt (~10 of 31 paired cases" in out

    def test_a_regression_carries_no_such_line(self):
        a = {f"c{i}": [True] for i in range(27)}
        b = {f"c{i}": [i >= 6] for i in range(27)}
        seen = {}
        findings, regressed = compare(
            normalise(draws_report(a, deterministic=True)),
            normalise(draws_report(b, deterministic=True)),
            seen=seen,
        )
        assert regressed and not any("minimum detectable" in f for f in findings)

    def test_the_closing_line_names_the_weakest_pairing_by_its_drop(self):
        # "few": 31 cases, none flipping back -- 33 pt at the 5 % floor.
        # "flippy": 42 cases, 6 worse and 6 better (14 % back) -- 35 pt.
        # Choosing by the fewest cases named 33 pt as the weakest.
        few = {f"c{i}": [True] for i in range(31)}

        def report(flippy):
            rows = [
                draws_report(few, label="few"),
                draws_report(flippy, label="flippy"),
            ]
            return normalise(
                {"benchmark": "bench_tools", "reports": [r["reports"][0] for r in rows]}
            )

        old = report({f"c{i}": [i >= 6] for i in range(42)})
        new = report({f"c{i}": [not 6 <= i < 12] for i in range(42)})
        seen = {}
        findings, regressed = compare(old, new, seen=seen)
        assert not regressed
        assert any("flippy" in f and "6 worse / 6 better" in f for f in findings)
        assert bcmp._mde_lines(seen) == [
            "flippy (weakest pairing): minimum detectable drop at 80% power: "
            "35pt (~15 of 42 paired cases; back-flip rate 14%, observed) — a "
            "smaller real drop is missed more than 20% of the time"
        ]

    def test_a_reused_seen_holds_only_the_last_comparison(self):
        seen = {}
        compare(tools_r3(), tools_r3(), seen=seen)
        compare(tools_r3(), tools_r3(), seen=seen)
        assert seen["paired"] == [("m", 42, 0)]


class TestPassK:
    def test_repeats_on_a_sampling_lane_print_pass_k(self):
        findings, _ = compare(tools_r3(), tools_r3())
        assert any(
            f.startswith("  m: pass^3 73% -> 73% (a case counts only when all 3")
            for f in findings
        )

    def test_a_deterministic_lane_prints_none(self):
        cases = {"a": [True] * 3, "b": [False] * 3}
        r = normalise(draws_report(cases, deterministic=True))
        assert not any("pass^" in f for f in compare(r, r)[0])

    def test_single_draws_print_none(self):
        r = normalise(draws_report({"a": [True], "b": [False]}))
        assert not any("pass^" in f for f in compare(r, r)[0])

    def test_different_repeats_compare_at_the_smaller_k(self):
        old = normalise(draws_report({"a": [True, True, False], "b": [True] * 3}))
        new = normalise(draws_report({"a": [True] * 4 + [False], "b": [True] * 5}))
        # old: (0 + 1) / 2; new: C(4,3)/C(5,3) = 0.4 and 1 -> 0.7.
        assert any("pass^3 50% -> 70%" in f for f in compare(old, new)[0])
