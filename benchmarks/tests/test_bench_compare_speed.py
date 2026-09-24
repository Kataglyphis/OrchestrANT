"""The speed runner's tripwire and the "nothing compared" verdict.

Split from test_bench_compare.py, which is frozen at its size: these pin
compare_speed.py and the exit-2 path bench_compare gained with it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_compare import _per_attempt, compare, normalise  # noqa: E402


def speed_report(decode, other_cores=None, lane_cores=None, energy=None):
    """A speed runner report: per-prompt decode rates, nothing else scored."""
    rows = []
    for i, rate in enumerate(decode):
        row = {
            "prompt_index": i,
            "latency_s": 5.0,
            "decode_tok_per_sec": rate,
            "completion_tokens": 100,
        }
        if other_cores is not None:
            row["other_cores"] = other_cores
        if lane_cores is not None:
            row["lane_cores"] = lane_cores
        if energy is not None:
            row["cpu_rail_energy_j"] = energy
        rows.append(row)
    return {"model": "m", "results": rows, "config": {}, "correctness": None}


class TestSpeedTripwire:
    """The legacy speed shape was reduced to its correctness score plus summed
    latency: v0.6.1 -> v0.7.0 lost 13 % of NPU decode and bench_compare said
    'no regression detected', and a CPU-lane pair compared nothing at all."""

    NPU_V061 = [22.8, 23.0, 23.3, 22.9, 21.6, 23.1, 22.7, 23.0, 21.3]

    def test_a_thirteen_percent_decode_drop_is_slower(self):
        old = normalise(speed_report(self.NPU_V061))
        new = normalise(speed_report([r * 0.87 for r in self.NPU_V061]))
        findings, regressed = compare(old, new)
        assert regressed
        assert any("decode" in f and "SLOWER" in f for f in findings)

    def test_run_to_run_noise_is_not(self):
        old = normalise(speed_report(self.NPU_V061))
        new = normalise(speed_report([r * 0.98 for r in self.NPU_V061]))
        assert compare(old, new)[1] is False

    def test_a_loaded_cpu_lane_is_reported_not_judged(self):
        old = normalise(speed_report([30.0] * 9, other_cores=0.1, lane_cores=7.4))
        new = normalise(speed_report([18.0] * 9, other_cores=0.9, lane_cores=7.4))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("NOT judged" in f for f in findings)

    def test_energy_is_reported_as_a_ratio_of_sums(self):
        old = normalise(speed_report([20.0] * 3, energy=10.0))
        new = normalise(speed_report([20.0] * 3, energy=12.0))
        findings, _ = compare(old, new)
        assert any("0.100 -> 0.120 J/token gross" in f for f in findings)

    def test_speed_reports_get_no_latency_verdict(self):
        # The summed prompt wall divided by the correctness count read "9.87 s
        # per attempt"; per prompt, a 256 -> 2048 max_tokens change then read
        # "+401 % *** SLOWER ***" with decode unchanged. The rates judge.
        entry = normalise(speed_report([20.0] * 9))["entries"][0]
        assert _per_attempt(entry) == (None, None)
        old, new = speed_report([20.0] * 9), speed_report([20.1] * 9)
        old["config"], new["config"] = {"max_tokens": 256}, {"max_tokens": 2048}
        for r in new["results"]:
            r["latency_s"] = 40.0
        findings, regressed = compare(normalise(old), normalise(new))
        assert not regressed and not any("per attempt" in f for f in findings)

    def test_a_loaded_OLD_run_does_not_hide_a_slower_new_one(self):
        # Load only lowers a CPU lane: a quiet new run that is still slower
        # than a loaded old one is a regression, not noise.
        old = normalise(speed_report([22.0] * 9, other_cores=0.9, lane_cores=7.4))
        new = normalise(speed_report([15.0] * 9, other_cores=0.1, lane_cores=7.4))
        findings, regressed = compare(old, new)
        assert regressed and any("SLOWER" in f for f in findings)

    def test_faster_against_a_loaded_old_run_is_not_judged(self):
        old = normalise(speed_report([18.0] * 9, other_cores=0.9, lane_cores=7.4))
        new = normalise(speed_report([30.0] * 9, other_cores=0.1, lane_cores=7.4))
        findings, _ = compare(old, new)
        assert any("faster, NOT judged" in f for f in findings)

    def test_older_reports_have_their_load_derived(self):
        # Pre-r2 reports carry cpu_percent and lane_cores but no other_cores:
        # 0.9 other cores on 8 must not read as a quiet machine.
        def legacy(rate, cpu_percent):
            r = speed_report([rate] * 9, lane_cores=7.3)
            r["hardware"] = {"cpu_total_threads": 8}
            for row in r["results"]:
                row["cpu_percent"], row["cpu_percent_method"] = cpu_percent, "window"
            return normalise(r)

        findings, regressed = compare(legacy(30.0, 92.5), legacy(18.0, 102.0 * 0.99))
        assert not regressed and any("NOT judged" in f for f in findings)


class TestNothingCompared:
    def test_contract_reports_point_at_their_own_diff(self):
        contract = {
            "benchmark": "bench_contract",
            "provenance": {},
            "config": {},
            "reports": [{"label": "npu", "model": "m", "checks": []}],
        }
        seen = {}
        findings, regressed = compare(
            normalise(contract), normalise(contract), seen=seen
        )
        assert not regressed and seen["compared"] == 0
        assert any("contract --diff" in f for f in findings)

    def test_reports_with_nothing_in_common_are_not_a_pass(self):
        empty = {"model": "m", "results": [], "config": {}, "correctness": None}
        seen = {}
        compare(normalise(empty), normalise(empty), seen=seen)
        assert seen["compared"] == 0


class TestGraderSelfcheckNoise:
    def test_timing_and_the_process_ceiling_are_not_a_config_change(self):
        # Every coding pair warned "not like-for-like" on 0.87 s vs 0.95 s.
        def with_check(seconds, ceiling):
            r = {
                "benchmark": "bench_coding",
                "provenance": {},
                "reports": [{"label": "m", "model": "m", "passed": 1, "total": 1}],
            }
            r["config"] = {
                "grader_selfcheck": {
                    "tasks": 33,
                    "seconds": seconds,
                    "rlimits": {"nproc": 64, "nproc_ceiling": ceiling},
                }
            }
            return normalise(r)

        findings, _ = compare(with_check(0.87, 171), with_check(0.95, 169))
        assert not any("grader_selfcheck" in f for f in findings)
        findings, _ = compare(with_check(0.87, 171), with_check(0.87, 171))
        assert not any("grader_selfcheck" in f for f in findings)


class TestLaneThroughputIsLikeForLike:
    def test_a_delivered_aggregate_is_compared_with_a_sum_only_as_sums(self):
        from bench_compare import _tps_pair

        old = {"delivered": False, "tok_per_sec": 15.5, "summed_tok_per_sec": 15.5}
        new = {"delivered": True, "tok_per_sec": 13.3, "summed_tok_per_sec": 15.4}
        assert _tps_pair(old, new) == (15.5, 15.4)
        both = {"delivered": True, "tok_per_sec": 13.3, "summed_tok_per_sec": 15.4}
        assert _tps_pair(both, both) == (13.3, 13.3)


class TestSuspectCasesKeepTheUnit:
    def test_agreeing_repeats_stay_one_observation_per_case(self):
        from bench_compare import mark_suspect_cases

        def rows(passes):
            return [
                {"case": c, "attempt": a, "passed": p}
                for c, p in passes.items()
                for a in range(3)
            ]

        control = {
            "label": "control",
            "backend": "control",
            "results": rows({"ok": True, "bad": False, "x": True}),
        }
        cand = {
            "label": "cand",
            "deterministic": False,
            "repeats_agreed": True,
            "results": rows({"ok": True, "bad": False, "x": False}),
        }
        mark_suspect_cases([control, cand])
        assert cand["effective_n"] == 2 and cand["effective_k"] == 1


class TestSuspectWallLeavesTheTiming:
    """P1.3's known gap: wall_measured_s kept the suspect case's seconds while
    total dropped the case, so the timing verdict charged them per attempt."""

    @staticmethod
    def _pair():
        control = {
            "label": "control",
            "backend": "control",
            "results": [{"case": "bad", "passed": False}],
        }
        lane = {
            "label": "lane",
            "passed": 1,
            "total": 3,
            "deterministic": False,
            "wall_measured_s": 26.0,
            "median_wall_s": 4.0,
            "results": [
                {"case": "ok", "passed": True, "wall_s": 2.0},
                {"case": "ok2", "passed": False, "wall_s": 4.0},
                {"case": "bad", "passed": False, "wall_s": 20.0},
            ],
        }
        return control, lane

    def test_every_wall_statistic_the_producer_wrote_is_recomputed(self):
        from bench_compare import mark_suspect_cases

        control, lane = self._pair()
        assert mark_suspect_cases([control, lane]) == ["bad"]
        assert lane["total"] == 2
        assert lane["wall_measured_s"] == 6.0 and lane["median_wall_s"] == 3.0
        # No total_wall_s was written, and none appears.
        assert "total_wall_s" not in lane and "avg_wall_s" not in lane

    def test_the_timing_verdict_no_longer_pays_for_the_suspect_case(self):
        from bench_compare import mark_suspect_cases

        control, lane = self._pair()
        mark_suspect_cases([control, lane])
        entry = normalise({"benchmark": "bench_tools", "reports": [lane]})
        # 26 s over the 2 kept attempts read 13 s each; the kept rows say 3 s.
        assert _per_attempt(entry["entries"][0]) == (3.0, "measured")
