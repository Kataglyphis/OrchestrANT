"""upgrade_check's speed step fails on the probe's integrity answers only.

On 2026-09-24 the CPU lane's Qwen3-4B-Instruct-2507 counted the r's in
"strawberry" as 5 on a healthy lane, and any upgrade check run with the
gate would have failed its speed step on that one capability miss: the step
failed on `wrong`, which counted every item. Nothing here runs a tool.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import upgrade_check as uc  # noqa: E402

from orchestrant.benchmark.correctness import (  # noqa: E402
    CORRECTNESS_PROBES,
    INTEGRITY,
    errored_item,
    graded_item,
    summarise,
)

GATED = {"kind": "speed", "argv": ["speed", "--stream", "--correctness"]}
LLAMA = os.path.join(
    HERE, "benchmark_results", "2026-09-24-roadmap", "cpu-llama3b-speed-answer.json"
)


def speed_report(block):
    config = {"prompts_requested": 9, "prompts_completed": 9}
    return {"model": "m", "results": [], "config": config, "correctness": block}


def probe_block(wrong=()):
    """A fresh probe block; items whose expected value is in `wrong` answer 0."""
    return summarise(
        [
            graded_item(p, "0" if p.accepted[0] in wrong else p.accepted[0])
            for p in CORRECTNESS_PROBES
        ]
    )


class TestTheSpeedStepGate:
    """A capability miss is the model's; only an integrity miss fails the step."""

    def test_a_capability_miss_passes_the_step(self):
        report = speed_report(probe_block(wrong={"3", "5", "9.9"}))
        assert uc._report_problem(GATED, report) is None

    def test_an_integrity_miss_fails_it(self):
        report = speed_report(probe_block(wrong={"391"}))
        integrity = sum(1 for p in CORRECTNESS_PROBES if p.kind == INTEGRITY)
        reason = uc._report_problem(GATED, report)
        assert reason == f"correctness gate (integrity): 1 of {integrity} answers wrong"

    def test_an_old_report_fails_only_on_its_arithmetic(self):
        # Written before kinds: Llama-3.2-3B's 3/6 is three capability misses.
        if not os.path.exists(LLAMA):
            pytest.skip("benchmark_results not present")
        with open(LLAMA, encoding="utf-8") as f:
            block = json.load(f)["correctness"]
        assert block["wrong"] == 3
        assert uc._report_problem(GATED, speed_report(block)) is None

    def test_a_block_without_items_is_judged_whole_as_before(self):
        block = {"score": 4, "total": 6, "wrong": 2, "truncated": 0, "errors": 0}
        reason = uc._report_problem(GATED, speed_report(block))
        assert "2 of 6 answers wrong" in reason

    def test_no_integrity_answer_scored_fails_it(self):
        items = [
            errored_item(p, OSError("reset"))
            if p.kind == INTEGRITY
            else graded_item(p, p.accepted[0])
            for p in CORRECTNESS_PROBES
        ]
        reason = uc._report_problem(GATED, speed_report(summarise(items)))
        assert "gate scored no probe" in reason

    def test_without_the_gate_a_wrong_answer_is_not_read(self):
        ungated = {"kind": "speed", "argv": ["speed", "--stream"]}
        report = speed_report(probe_block(wrong={"391"}))
        assert uc._report_problem(ungated, report) is None
