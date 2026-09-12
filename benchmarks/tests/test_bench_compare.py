"""Tests for the regression comparer.

A comparer is only useful if it is trustworthy in both directions: it must
catch a real regression, and it must NOT cry wolf. A tripwire that fires on
noise gets muted, and a muted tripwire is the same as none.

The three ways to be confidently wrong, each tested here:
  * calling a difference a regression the sample cannot support,
  * blaming the model when the grader changed,
  * comparing two different things and not noticing.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_compare import compare, load, normalise, pair_directories  # noqa: E402


def report(entries, benchmark="bench_tools", prov=None):
    return {
        "benchmark": benchmark,
        "provenance": prov or {},
        "config": {},
        "reports": [
            dict(
                label=l,
                model=l,
                passed=p,
                total=t,
                total_wall_s=w,
                median_wall_s=None,
                effective_n=t,
                deterministic=False,
            )
            for l, p, t, w in entries
        ],
    }


class TestNormalisation:
    def test_reads_the_new_envelope(self):
        n = normalise(report([("m", 8, 12, 20.0)]))
        assert n["benchmark"] == "bench_tools"
        assert n["entries"][0]["passed"] == 8

    def test_reads_the_older_benchmark_openai_api_envelope(self):
        # The suite emits two shapes; refusing one would leave half the history
        # uncomparable.
        old = {
            "model": "m",
            "hardware": {"host": "h"},
            "config": {},
            "correctness": {"score": 5, "total": 6},
            "results": [{"latency_s": 1.5}, {"latency_s": 2.5}],
        }
        n = normalise(old)
        assert n["benchmark"] == "benchmark_openai_api"
        assert n["entries"][0]["passed"] == 5 and n["entries"][0]["wall_s"] == 4.0

    def test_a_run_without_a_correctness_probe_has_no_score(self):
        n = normalise({"model": "m", "results": [{"latency_s": 1.0}]})
        assert n["entries"][0]["passed"] is None


class TestRegressionDetection:
    def test_a_large_drop_is_a_regression(self):
        old = normalise(report([("m", 28, 30, 10.0)]))
        new = normalise(report([("m", 5, 30, 10.0)]))
        findings, regressed = compare(old, new)
        assert regressed and any("REGRESSION" in f for f in findings)

    def test_a_drop_the_sample_cannot_support_is_NOT_a_regression(self):
        # 12/12 -> 8/12 looks alarming and is not separable at n=12. Firing here
        # is how a tripwire gets muted.
        old = normalise(report([("m", 12, 12, 10.0)]))
        new = normalise(report([("m", 8, 12, 10.0)]))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("not separable" in f.lower() for f in findings)

    def test_an_improvement_is_not_flagged(self):
        old = normalise(report([("m", 5, 30, 10.0)]))
        new = normalise(report([("m", 28, 30, 10.0)]))
        _, regressed = compare(old, new)
        assert not regressed

    def test_unchanged_scores_are_reported_as_such(self):
        old = normalise(report([("m", 9, 9, 10.0)]))
        findings, regressed = compare(old, old)
        assert not regressed and any("unchanged" in f for f in findings)


class TestTiming:
    def test_a_large_slowdown_is_a_regression(self):
        old = normalise(report([("m", 9, 9, 10.0)]))
        new = normalise(report([("m", 9, 9, 20.0)]))
        findings, regressed = compare(old, new)
        assert regressed and any("SLOWER" in f for f in findings)

    def test_small_timing_noise_is_tolerated(self):
        old = normalise(report([("m", 9, 9, 10.0)]))
        new = normalise(report([("m", 9, 9, 11.0)]))
        _, regressed = compare(old, new)
        assert not regressed, (
            "10% is noise on this hardware; firing here mutes the alarm"
        )

    def test_the_tolerance_is_adjustable(self):
        old = normalise(report([("m", 9, 9, 10.0)]))
        new = normalise(report([("m", 9, 9, 11.0)]))
        _, regressed = compare(old, new, time_tolerance=0.05)
        assert regressed


class TestProvenanceGuards:
    def test_a_changed_grader_is_surfaced(self):
        # The trap: the ranking moved because the BENCHMARK changed.
        old = normalise(report([("m", 9, 9, 10.0)], prov={"tool_sha256": "aaa"}))
        new = normalise(report([("m", 9, 9, 10.0)], prov={"tool_sha256": "bbb"}))
        findings, _ = compare(old, new)
        assert any("BENCHMARK SOURCE CHANGED" in f for f in findings)

    def test_comparing_different_benchmarks_is_refused(self):
        old = normalise(report([("m", 9, 9, 10.0)], benchmark="bench_tools"))
        new = normalise(report([("m", 9, 9, 10.0)], benchmark="bench_coding"))
        findings, regressed = compare(old, new)
        assert regressed and any("different benchmarks" in f for f in findings)


class TestEntrySetChanges:
    def test_a_disappeared_model_is_reported(self):
        old = normalise(report([("a", 9, 9, 10.0), ("b", 9, 9, 10.0)]))
        new = normalise(report([("a", 9, 9, 10.0)]))
        findings, _ = compare(old, new)
        assert any("missing from the new one" in f for f in findings)

    def test_a_new_model_is_reported_without_failing(self):
        old = normalise(report([("a", 9, 9, 10.0)]))
        new = normalise(report([("a", 9, 9, 10.0), ("b", 9, 9, 10.0)]))
        findings, regressed = compare(old, new)
        assert not regressed and any("no baseline" in f for f in findings)


class TestRoundTrip:
    def test_loads_a_report_written_to_disk(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps(report([("m", 9, 9, 10.0)])))
        assert load(str(p))["entries"][0]["passed"] == 9


def report_with_cases(
    label, cases, wall=10.0, benchmark="bench_tools", prov=None, deterministic=True
):
    """A report carrying per-case detail, as the real tools emit."""
    results = [{"case": k, "passed": v} for k, v in cases.items()]
    passed = sum(1 for v in cases.values() if v)
    return {
        "benchmark": benchmark,
        "provenance": prov or {},
        "config": {},
        "reports": [
            {
                "label": label,
                "model": label,
                "passed": passed,
                "total": len(cases),
                "total_wall_s": wall,
                "median_wall_s": None,
                "effective_n": len(cases),
                "deterministic": deterministic,
                "results": results,
            }
        ],
    }


class TestPerCaseComparison:
    """The aggregate is the weaker test. On a deterministic endpoint a case that
    flipped is a concrete, attributable change, and it needs no statistics --
    which matters because the measured 93%->81% degradation would need 119
    cases to clear a confidence interval, while naming the broken cases needs
    none."""

    def test_a_flipped_case_is_a_regression_even_when_the_aggregate_is_not(self):
        old = normalise(report_with_cases("m", {f"c{i}": True for i in range(27)}))
        new_cases = {f"c{i}": True for i in range(27)}
        for i in range(3):
            new_cases[f"c{i}"] = False
        new = normalise(report_with_cases("m", new_cases))
        findings, regressed = compare(old, new)
        assert regressed, "three named cases broke; that is not noise"
        assert any("case(s) that PASSED now fail" in f for f in findings)

    def test_the_broken_cases_are_named(self):
        old = normalise(report_with_cases("m", {"a": True, "b": True, "c": True}))
        new = normalise(report_with_cases("m", {"a": True, "b": False, "c": True}))
        findings, _ = compare(old, new)
        assert any("broke: b" in f for f in findings)

    def test_fixed_cases_are_reported_without_failing(self):
        old = normalise(report_with_cases("m", {"a": False, "b": True}))
        new = normalise(report_with_cases("m", {"a": True, "b": True}))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("now fixed" in f for f in findings)

    def test_a_swap_still_regresses_even_at_an_identical_score(self):
        # Same 1/2 both times, but a different case passes. The aggregate sees
        # nothing; that is precisely the blind spot this closes.
        old = normalise(report_with_cases("m", {"a": True, "b": False}))
        new = normalise(report_with_cases("m", {"a": False, "b": True}))
        findings, regressed = compare(old, new)
        assert regressed and any("broke: a" in f for f in findings)

    def test_new_cases_absent_from_the_baseline_are_ignored(self):
        # Adding a case the baseline never ran must not read as a regression.
        old = normalise(report_with_cases("m", {"a": True}))
        new = normalise(report_with_cases("m", {"a": True, "brand_new": False}))
        _, regressed = compare(old, new)
        assert not regressed

    def test_reports_without_per_case_detail_still_compare(self):
        old = normalise(report([("m", 28, 30, 10.0)]))
        new = normalise(report([("m", 5, 30, 10.0)]))
        _, regressed = compare(old, new)
        assert regressed, "the aggregate path must keep working for older reports"


def report_repeats(
    label, cases, wall=10.0, deterministic=False, config=None, errored=()
):
    """A report with N attempts per case, as --repeats produces."""
    results = []
    for name, outcomes in cases.items():
        for i, ok in enumerate(outcomes):
            results.append(
                {"case": name, "attempt": i, "passed": ok, "errored": name in errored}
            )
    passed = sum(1 for v in cases.values() for ok in v if ok)
    total = sum(len(v) for v in cases.values())
    return {
        "benchmark": "bench_tools",
        "provenance": {},
        "config": config or {},
        "reports": [
            {
                "label": label,
                "model": label,
                "passed": passed,
                "total": total,
                "total_wall_s": wall,
                "median_wall_s": wall / max(total, 1),
                "effective_n": len(cases) if deterministic else total,
                "deterministic": deterministic,
                "results": results,
            }
        ],
    }


class TestRepeatsAreNotCollapsed:
    """Collapsing repeats to a bool with all() was wrong in BOTH directions:
    it fired on one flaky draw, and it went silent on a total collapse."""

    def test_one_flaky_draw_does_not_fire_the_alarm(self):
        # 3/3 -> 2/3 on a sampling lane. Firing here mutes the tripwire.
        old = normalise(report_repeats("m", {"a": [True, True, True]}))
        new = normalise(report_repeats("m", {"a": [True, True, False]}))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("pass less often" in f for f in findings), (
            "the degradation should still be visible, just not alarmed"
        )

    def test_a_total_per_case_collapse_is_caught(self):
        # 2/3 -> 0/3. The old all() rule produced False -> False: nothing at all.
        old = normalise(
            report_repeats("m", {"a": [True, True, False], "b": [True, True, True]})
        )
        new = normalise(
            report_repeats("m", {"a": [False, False, False], "b": [True, True, True]})
        )
        findings, regressed = compare(old, new)
        assert regressed and any("broke: a" in f for f in findings)

    def test_transport_errors_are_excluded_from_the_case_view(self):
        old = normalise(report_repeats("m", {"a": [True]}))
        new = normalise(report_repeats("m", {"a": [False]}, errored=("a",)))
        findings, regressed = compare(old, new)
        assert not regressed, "an HTTP failure is not the model failing"


class TestConfigIsCompared:
    """Dropping --system or changing --repeats changes what the numbers MEAN.
    Both used to surface as the model regressing."""

    def test_a_changed_system_prompt_is_called_out(self):
        old = normalise(
            report_repeats(
                "m", {"a": [True]}, config={"system_prompt": "p.md", "repeats": 1}
            )
        )
        new = normalise(
            report_repeats(
                "m", {"a": [True]}, config={"system_prompt": None, "repeats": 1}
            )
        )
        findings, _ = compare(old, new)
        assert any("config.system_prompt changed" in f for f in findings)

    def test_timing_is_not_compared_across_a_repeats_change(self):
        # total_wall_s scales linearly with repeats; comparing raw totals
        # reported a slowdown for doing three times the work.
        old = normalise(
            report_repeats("m", {"a": [True]}, wall=10.0, config={"repeats": 1})
        )
        new = normalise(
            report_repeats(
                "m", {"a": [True, True, True]}, wall=30.0, config={"repeats": 3}
            )
        )
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("timing not compared" in f for f in findings)


class TestTimingUsesPerAttempt:
    def test_a_run_that_errored_out_is_not_reported_as_faster(self):
        # Summing only successful requests made a half-failed run look quick.
        old = normalise(
            report_repeats(
                "m",
                {"a": [True], "b": [True], "c": [True]},
                wall=30.0,
                config={"repeats": 1},
            )
        )
        new = normalise(
            report_repeats(
                "m",
                {"a": [True], "b": [True], "c": [True]},
                wall=60.0,
                config={"repeats": 1},
            )
        )
        findings, regressed = compare(old, new)
        assert regressed and any("SLOWER" in f for f in findings)
        assert any("per attempt" in f for f in findings)


class TestIntervalsUseEffectiveN:
    def test_repeats_on_a_deterministic_lane_do_not_manufacture_significance(self):
        # 5 cases repeated 4x looks like n=20; it is 5 observations.
        cases_old = {f"c{i}": [True] * 4 for i in range(5)}
        cases_new = {f"c{i}": [True] * 4 for i in range(5)}
        cases_new["c0"] = [False] * 4
        old = normalise(
            report_repeats("m", cases_old, deterministic=True, config={"repeats": 4})
        )
        new = normalise(
            report_repeats("m", cases_new, deterministic=True, config={"repeats": 4})
        )
        findings, _ = compare(old, new)
        score_line = next(f for f in findings if "->" in f and "/" in f)
        assert "/5" in score_line, f"intervals should use effective_n=5: {score_line}"


class TestDirectoryPairing:
    """Run-to-run comparison over whole result directories.

    The comparer existed and nothing ever called it; the sweep writes one report
    per config, so comparing two runs means pairing those files up. Both
    directions matter here too: a real regression in any config must fail, and a
    config that quietly disappeared must not pass as "nothing to report".
    """

    def _run_dir(self, tmp_path, name, entries):
        d = tmp_path / name
        d.mkdir()
        for fname, ent in entries.items():
            (d / fname).write_text(json.dumps(report(ent)))
        return d

    def test_pairs_by_file_name(self, tmp_path):
        old = self._run_dir(
            tmp_path,
            "old",
            {"a.json": [("m", 9, 10, 5.0)], "b.json": [("m", 9, 10, 5.0)]},
        )
        new = self._run_dir(
            tmp_path,
            "new",
            {"a.json": [("m", 9, 10, 5.0)], "c.json": [("m", 9, 10, 5.0)]},
        )
        pairs, only_new, only_old = pair_directories(str(old), str(new))
        assert [p[0] for p in pairs] == ["a.json"]
        assert only_new == ["c.json"] and only_old == ["b.json"]

    def test_manifest_is_an_index_not_a_report(self, tmp_path):
        old = self._run_dir(tmp_path, "old", {"a.json": [("m", 9, 10, 5.0)]})
        new = self._run_dir(tmp_path, "new", {"a.json": [("m", 9, 10, 5.0)]})
        for d in (old, new):
            (d / "_manifest.json").write_text("{}")
        pairs, _, _ = pair_directories(str(old), str(new))
        assert [p[0] for p in pairs] == ["a.json"]

    def _cli(self, old, new):
        import subprocess

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run(
            [
                sys.executable,
                os.path.join(here, "bench_compare.py"),
                "--dir",
                str(old),
                str(new),
            ],
            capture_output=True,
            text=True,
        )

    def test_a_real_regression_in_any_config_fails(self, tmp_path):
        old = self._run_dir(
            tmp_path,
            "old",
            {"a.json": [("m", 10, 10, 5.0)], "b.json": [("m", 10, 10, 5.0)]},
        )
        new = self._run_dir(
            tmp_path,
            "new",
            {"a.json": [("m", 10, 10, 5.0)], "b.json": [("m", 2, 10, 5.0)]},
        )
        r = self._cli(old, new)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "REGRESSION" in r.stdout

    def test_identical_runs_do_not_cry_wolf(self, tmp_path):
        same = {"a.json": [("m", 9, 10, 5.0)], "b.json": [("m", 8, 10, 6.0)]}
        old = self._run_dir(tmp_path, "old", same)
        new = self._run_dir(tmp_path, "new", same)
        r = self._cli(old, new)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "2 report(s) compared" in r.stdout

    def test_a_vanished_config_is_reported_not_swallowed(self, tmp_path):
        old = self._run_dir(
            tmp_path,
            "old",
            {"a.json": [("m", 9, 10, 5.0)], "gone.json": [("m", 9, 10, 5.0)]},
        )
        new = self._run_dir(tmp_path, "new", {"a.json": [("m", 9, 10, 5.0)]})
        r = self._cli(old, new)
        assert "gone.json" in r.stdout and "1 gone" in r.stdout

    def test_no_common_reports_is_an_error_not_a_pass(self, tmp_path):
        old = self._run_dir(tmp_path, "old", {"a.json": [("m", 9, 10, 5.0)]})
        new = self._run_dir(tmp_path, "new", {"z.json": [("m", 9, 10, 5.0)]})
        r = self._cli(old, new)
        assert r.returncode != 0


def _flip(n_cases, n_flips, deterministic=False, prov=None, n_fixed=0):
    """old: all pass. new: the first n_flips fail; n_fixed cases fail in OLD
    and pass in new. One draw per case, as --repeats 1 produces."""
    old_cases = {
        f"c{i}": i >= n_cases - n_fixed and False or True for i in range(n_cases)
    }
    for i in range(n_cases - n_fixed, n_cases):
        old_cases[f"c{i}"] = False
    new_cases = {f"c{i}": i >= n_flips for i in range(n_cases)}
    old = normalise(
        report_with_cases("m", old_cases, deterministic=deterministic, prov=prov)
    )
    new = normalise(
        report_with_cases("m", new_cases, deterministic=deterministic, prov=prov)
    )
    return compare(old, new)


class TestPairedAggregate:
    """Both runs asked the same cases, so the aggregate is judged by the paired
    sign test, not by overlap of two intervals fitted as if independent."""

    def test_six_one_way_flips_regress_where_the_intervals_still_overlap(self):
        # 27/27 -> 21/27: the intervals overlap, the paired test says p=0.031.
        findings, regressed = _flip(27, 6)
        assert regressed
        assert any(
            "REGRESSION" in f and "paired sign test 6 worse" in f for f in findings
        )

    def test_the_same_aggregate_without_per_case_detail_falls_back_to_overlap(self):
        old = normalise(report([("m", 27, 27, 10.0)]))
        new = normalise(report([("m", 21, 27, 10.0)]))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("unpaired" in f for f in findings)

    def test_three_one_way_flips_are_not_separable(self):
        findings, regressed = _flip(27, 3)
        assert not regressed
        assert any("not separable" in f and "p=0.250" in f for f in findings)

    def test_a_swap_of_equal_size_is_a_tie_not_a_regression(self):
        # 6 broke and 6 fixed: p=1.0, and no rate moved.
        findings, regressed = _flip(27, 6, n_fixed=6)
        assert not regressed
        assert any("p=1.000" in f for f in findings)

    def test_an_improvement_is_marked_separable_by_the_paired_test(self):
        old = normalise(
            report_with_cases(
                "m", {f"c{i}": i >= 6 for i in range(27)}, deterministic=False
            )
        )
        new = normalise(
            report_with_cases(
                "m", {f"c{i}": True for i in range(27)}, deterministic=False
            )
        )
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("improved (separable)" in f for f in findings)

    def test_the_difference_interval_is_printed(self):
        findings, _ = _flip(27, 6)
        assert any("diff -22pt [" in f for f in findings)


class TestSingleDrawFlips:
    """At --repeats 1 on a lane nobody has shown deterministic, a per-case flip
    is one coin toss: the alarm fired on 92 % of same-model re-runs. It is
    named as such, and the paired test above decides the run."""

    def test_one_flip_is_reported_not_alarmed(self):
        findings, regressed = _flip(27, 1)
        assert not regressed
        assert any(
            "flipped (single draw — rerun with --repeats 3)" in f for f in findings
        )
        assert not any("PASSED now fail" in f for f in findings)

    def test_a_deterministic_lane_still_treats_a_flip_as_a_regression(self):
        findings, regressed = _flip(27, 1, deterministic=True)
        assert regressed and any("broke: c0" in f for f in findings)

    def test_a_provenance_probe_that_saw_two_draws_agree_counts_as_deterministic(self):
        prov = {"determinism_probe": {"deterministic": True}}
        _, regressed = _flip(27, 1, deterministic=False, prov=prov)
        assert regressed

    def test_a_probe_that_saw_disagreement_does_not(self):
        prov = {"determinism_probe": {"deterministic": False}}
        _, regressed = _flip(27, 1, deterministic=False, prov=prov)
        assert not regressed

    def test_repeats_above_one_keep_the_never_passes_now_rule(self):
        # 3 draws each: "passed before, never passes now" is a real claim.
        old = normalise(report_repeats("m", {"a": [True, True, True]}))
        new = normalise(report_repeats("m", {"a": [False, False, False]}))
        _, regressed = compare(old, new)
        assert regressed


def report_agent(label, rows, total_wall_s, wall_measured_s=None, total=None):
    """A bench_agent-shaped report: rows carry status and wall_s."""
    measured = [
        r for r in rows if r.get("status") != "CONTEXT" and not r.get("errored")
    ]
    rep = {
        "label": label,
        "model": label,
        "passed": sum(1 for r in measured if r["passed"]),
        "total": len(measured) if total is None else total,
        "total_wall_s": total_wall_s,
        "median_wall_s": None,
        "effective_n": len(measured),
        "deterministic": True,
        "results": rows,
    }
    if wall_measured_s is not None:
        rep["wall_measured_s"] = wall_measured_s
    return {
        "benchmark": "bench_agent",
        "provenance": {},
        "config": {"repeats": 1},
        "reports": [rep],
    }


class TestContextBlockedRows:
    """D16: a task whose prompt never fit the context was not attempted. It is
    not a regression, and its wall (often the timeout) is not the model's."""

    def test_a_blocked_task_neither_breaks_nor_fixes(self):
        old = normalise(
            report_agent("m", [{"task": "a", "passed": True, "wall_s": 1.0}], 1.0)
        )
        new = normalise(
            report_agent(
                "m",
                [{"task": "a", "passed": False, "status": "CONTEXT", "wall_s": 1.0}],
                1.0,
            )
        )
        findings, regressed = compare(old, new)
        assert not regressed and not any("broke" in f for f in findings)
        assert "a" not in new["entries"][0]["cases"]

    def test_a_blocked_flag_is_honoured_too(self):
        n = normalise(
            report_agent("m", [{"task": "a", "passed": False, "blocked": True}], 0.0)
        )
        assert n["entries"][0]["cases"] == {}

    def test_blocked_wall_does_not_decide_slower(self):
        rows_old = [
            {"task": "a", "passed": True, "wall_s": 1.0},
            {"task": "b", "passed": True, "wall_s": 1.0},
        ]
        rows_new = rows_old + [
            {"task": "c", "passed": False, "status": "CONTEXT", "wall_s": 100.0}
        ]
        # total_wall_s summed the blocked wall while total excluded the task:
        # 1.0 s -> 51 s per attempt, SLOWER, for a task never attempted.
        old = normalise(report_agent("m", rows_old, 2.0))
        new = normalise(report_agent("m", rows_new, 102.0))
        findings, regressed = compare(old, new)
        assert not regressed, findings
        assert any("1.00s -> 1.00s per attempt" in f for f in findings)


class TestMeasuredWall:
    """The SLOWER verdict uses wall over MEASURED attempts. A cut attempt can
    sit at the 1800 s deadline; letting it in decided the verdict."""

    def test_wall_measured_s_is_preferred_over_the_total(self):
        old = normalise(report_agent("m", [], 200.0, wall_measured_s=2.0, total=2))
        new = normalise(report_agent("m", [], 2000.0, wall_measured_s=2.2, total=2))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("1.00s -> 1.10s per attempt" in f for f in findings)

    def test_falling_back_to_the_total_is_noted(self):
        old = normalise(report([("m", 9, 9, 10.0)]))
        new = normalise(report([("m", 9, 9, 10.0)]))
        findings, _ = compare(old, new)
        assert any("legacy timing" in f for f in findings)

    def test_measured_rows_carry_no_legacy_note(self):
        rows = [{"task": "a", "passed": True, "wall_s": 1.0}]
        old = normalise(report_agent("m", rows, 1.0))
        findings, _ = compare(old, old)
        line = next(f for f in findings if "per attempt" in f)
        assert "legacy" not in line


class TestDuplicateLabels:
    """D27: two lanes serving the same model collapse to one label, and a
    dict keyed on label then reports whichever came last."""

    def test_normalise_refuses_colliding_labels(self):
        # The shipped geniex-gpu / geniex-cpu pair both pin this model, and the
        # label falls back to the model id when none is given.
        m = "unsloth/Qwen3-4B-GGUF:Q4_0"
        raw = report([(m, 3, 3, 1.0), (m, 0, 3, 1.0)])
        with pytest.raises(ValueError) as e:
            normalise(raw)
        assert m in str(e.value) and "label" in str(e.value)

    def test_load_names_the_file(self, tmp_path):
        p = tmp_path / "dup.json"
        p.write_text(json.dumps(report([("m", 3, 3, 1.0), ("m", 0, 3, 1.0)])))
        with pytest.raises(SystemExit) as e:
            load(str(p))
        assert "dup.json" in str(e.value)

    def test_distinct_labels_for_the_same_model_are_fine(self):
        raw = report([("gpu", 3, 3, 1.0), ("cpu", 0, 3, 1.0)])
        assert len(normalise(raw)["entries"]) == 2


class TestNotAReport:
    def test_a_json_that_is_neither_envelope_is_refused(self):
        with pytest.raises(ValueError):
            normalise({"prompt": "x", "max_tokens": 3})


def control_report(control_rows, candidate_rows, control_label=None, backend="control"):
    def row(rows, label, **extra):
        return {
            "label": label,
            "model": label,
            "passed": sum(1 for r in rows if r["passed"]),
            "total": len(rows),
            "total_wall_s": 1.0,
            "median_wall_s": None,
            "effective_n": len(rows),
            "deterministic": True,
            "results": rows,
            **extra,
        }

    return {
        "benchmark": "bench_tools",
        "provenance": {},
        "config": {},
        "reports": [
            row(control_rows, control_label or "gpt-strong", backend=backend),
            row(candidate_rows, "cand"),
        ],
    }


class TestSuspectCases:
    """D28: the control mechanism backends.json promises. A case the strongest
    endpoint also fails is evidence about the CASE, not the candidates."""

    ROWS = [
        {"case": "ok", "passed": True},
        {"case": "bad", "passed": False},
        {"case": "down", "passed": False, "errored": True},
        {"case": "blocked", "passed": False, "status": "CONTEXT"},
    ]

    def test_control_is_matched_by_backend_even_when_labelled_by_model(self):
        from bench_compare import suspect_cases

        raw = control_report(self.ROWS, [])
        assert suspect_cases(raw["reports"]) == ["bad"]

    def test_control_is_matched_by_a_label_prefix(self):
        from bench_compare import suspect_cases

        raw = control_report(
            self.ROWS, [], control_label="control-ollama", backend=None
        )
        assert suspect_cases(raw["reports"]) == ["bad"]

    def test_no_control_yields_none(self):
        from bench_compare import suspect_cases

        assert suspect_cases([{"label": "m", "results": self.ROWS}]) is None

    def test_normalise_carries_suspect_cases(self):
        assert normalise(control_report(self.ROWS, []))["suspect_cases"] == ["bad"]

    def test_compare_names_them_and_annotates_a_broken_suspect(self):
        old = normalise(control_report(self.ROWS, [{"case": "bad", "passed": True}]))
        new = normalise(control_report(self.ROWS, [{"case": "bad", "passed": False}]))
        findings, _ = compare(old, new)
        assert any("CONTROL also fails" in f and "bad" in f for f in findings)
        assert any("broke: bad (suspect" in f for f in findings)


def lanes_report(tok, serialised=None):
    reports = [
        {"label": name, "model": "m", "base_url": "http://h:1", "tok_per_sec": v}
        for name, v in tok.items()
    ]
    if serialised is not None:
        reports.append({"label": "batching", "model": "m", "serialised": serialised})
    return {
        "benchmark": "bench_lanes",
        "provenance": {},
        "config": {"max_tokens": 256},
        "reports": reports,
    }


class TestLanesReports:
    """D29: a lanes report used to normalise to nothing, so ANY two of them
    compared as 'no regression'. Throughput is diffed with a tolerance."""

    def test_a_large_throughput_drop_regresses(self):
        old = normalise(lanes_report({"npu": 19.0, "aggregate": 31.0}))
        new = normalise(lanes_report({"npu": 19.0, "aggregate": 20.0}))
        findings, regressed = compare(old, new)
        assert regressed and any(
            "aggregate: 31.0 -> 20.0 tok/s" in f and "SLOWER" in f for f in findings
        )

    def test_a_small_drop_is_noise(self):
        old = normalise(lanes_report({"aggregate": 31.0}))
        new = normalise(lanes_report({"aggregate": 29.0}))
        _, regressed = compare(old, new)
        assert not regressed

    def test_losing_batching_regresses(self):
        old = normalise(lanes_report({}, serialised=False))
        new = normalise(lanes_report({}, serialised=True))
        findings, regressed = compare(old, new)
        assert regressed and any("SERIALISES" in f for f in findings)

    def test_gaining_batching_does_not(self):
        old = normalise(lanes_report({}, serialised=True))
        new = normalise(lanes_report({}, serialised=False))
        _, regressed = compare(old, new)
        assert not regressed
