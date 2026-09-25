"""bench_compare scores a speed report's probe on its integrity items.

The correctness probe split its items into integrity (what a broken kernel
loses) and capability (what a small model cannot do) on 2026-09-25, and grew
from six items to ten. Every tracked speed report predates that. The rule
pinned here: an old report's items take their kinds from their prompts (the
kind belongs to the question, and the six prompts are unchanged), the score
compared is the integrity score paired by prompt over the items both reports
measured, and capability answers are listed when they move, never judged.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from bench_compare import compare, normalise  # noqa: E402
from compare_speed import PROBE_RERUN  # noqa: E402

from orchestrant.benchmark.correctness import (  # noqa: E402
    CORRECTNESS_PROBES,
    INTEGRITY,
    errored_item,
    graded_item,
    summarise,
)

LLAMA = os.path.join(
    HERE, "benchmark_results", "2026-09-24-roadmap", "cpu-llama3b-speed-answer.json"
)
MULTIPLY = "What is 23 * 17? Reply with only the number."
SQUARE = "What is 17 squared? Reply with only the number."
STRAWBERRY = "How many times does the letter 'r' appear in the word strawb"
DECIMALS = "Which number is larger, 9.11 or 9.9? Reply with only the num"
INTEGRITY_ITEMS = sum(1 for p in CORRECTNESS_PROBES if p.kind == INTEGRITY)
# Every integrity item's expected answer: probe_block(wrong=...) loses them all.
EVERY_INTEGRITY = {p.accepted[0] for p in CORRECTNESS_PROBES if p.kind == INTEGRITY}


def speed(block):
    return {"model": "m", "results": [], "config": {}, "correctness": block}


def probe_block(wrong=(), cut=()):
    """A block the current probe writes; `wrong` answer 0, `cut` never finish."""
    items = []
    for p in CORRECTNESS_PROBES:
        content = p.accepted[0]
        if content in wrong:
            content = "0"
        if content in cut:
            content = "<think>still going"
        items.append(graded_item(p, content))
    return summarise(items)


def llama_block():
    """Llama-3.2-3B's 2026-09-24 block: 3/6, written before kinds existed."""
    if not os.path.exists(LLAMA):
        pytest.skip("benchmark_results not present")
    with open(LLAMA, encoding="utf-8") as f:
        return json.load(f)["correctness"]


def entry(block):
    return normalise(speed(block))["entries"][0]


class TestTheScore:
    """What a speed entry's passed/total/cases are."""

    def test_an_old_report_scores_its_two_arithmetic_items(self):
        old = entry(llama_block())
        assert (old["passed"], old["total"]) == (2, 2)
        assert old["cases"] == {MULTIPLY: (1, 1), SQUARE: (1, 1)}

    def test_its_capability_answers_ride_along_unscored(self):
        capability = entry(llama_block())["capability"]
        assert len(capability) == 4
        assert capability[STRAWBERRY] is False

    def test_a_new_report_scores_every_integrity_item(self):
        new = entry(probe_block(wrong={"3"}))
        assert (new["passed"], new["total"]) == (INTEGRITY_ITEMS, INTEGRITY_ITEMS)

    def test_a_cut_integrity_answer_was_not_measured(self):
        new = entry(probe_block(cut={"289"}))
        assert new["total"] == INTEGRITY_ITEMS - 1
        assert SQUARE not in new["cases"]

    def test_an_errored_integrity_answer_was_not_measured_either(self):
        # A transport failure says nothing about the model (measured()): it
        # must neither lower the score nor pair as a flip.
        items = [
            errored_item(p, OSError("reset"))
            if p.accepted[0] == "289"
            else graded_item(p, p.accepted[0])
            for p in CORRECTNESS_PROBES
        ]
        new = entry(summarise(items))
        assert (new["passed"], new["total"]) == (INTEGRITY_ITEMS - 1,) * 2
        assert SQUARE not in new["cases"]

    def test_a_block_without_items_is_scored_whole_as_before(self):
        old = entry({"score": 5, "total": 6})
        assert (old["passed"], old["total"]) == (5, 6)
        assert "cases" not in old


class TestOldAgainstNew:
    """The first comparison across the change: 6 items without kinds vs 10."""

    def test_capability_misses_on_both_sides_are_no_regression(self):
        old = normalise(speed(llama_block()))
        new = normalise(speed(probe_block(wrong={"3", "5", "9.9"})))
        findings, regressed = compare(old, new)
        assert not regressed
        text = "\n".join(findings)
        assert "capability 1/4 -> 1/4" in text
        assert "not judged" in text
        assert (
            f"{INTEGRITY_ITEMS - 2} integrity item(s) measured by one report only"
            in text
        )

    def test_an_integrity_item_that_broke_is_named_with_the_probe_remedy(self):
        old = normalise(speed(llama_block()))
        new = normalise(speed(probe_block(wrong={"391", "3", "5", "9.9"})))
        findings, _ = compare(old, new)
        flipped = [f for f in findings if "flipped" in f]
        assert len(flipped) == 1
        assert MULTIPLY in flipped[0]
        assert PROBE_RERUN in flipped[0]
        assert "--repeats" not in flipped[0]

    def test_a_capability_move_is_listed_not_judged(self):
        old = normalise(speed(llama_block()))
        # Strawberry now right, 9.9 vs 9.11 still wrong, the puzzle now right.
        new = normalise(speed(probe_block(wrong={"9.9"})))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any(f.strip() == f"now right: {STRAWBERRY}" for f in findings)
        assert not any(DECIMALS in f for f in findings)


class TestNewAgainstNew:
    """Two reports with kinds pair over every integrity item."""

    def test_every_integrity_answer_lost_is_a_regression(self):
        integrity = {p.accepted[0] for p in CORRECTNESS_PROBES if p.kind == INTEGRITY}
        old = normalise(speed(probe_block()))
        new = normalise(speed(probe_block(wrong=integrity)))
        findings, regressed = compare(old, new)
        assert regressed
        assert any("REGRESSION" in f for f in findings)

    def test_every_capability_answer_lost_is_not(self):
        capability = {p.accepted[0] for p in CORRECTNESS_PROBES if p.kind != INTEGRITY}
        old = normalise(speed(probe_block()))
        new = normalise(speed(probe_block(wrong=capability)))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("capability 4/4 -> 0/4" in f for f in findings)

    def test_a_capability_answer_lost_is_named(self):
        old = normalise(speed(probe_block()))
        new = normalise(speed(probe_block(wrong={"3"})))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any(f.strip() == f"now wrong: {STRAWBERRY}" for f in findings)


class TestACollapse:
    """A probe whose integrity verdict became BROKEN is a REGRESSION.

    Before kinds, a stored 6/6 against a broken 0/6 read *** REGRESSION ***
    (unpaired, every item). Paired over the two arithmetic items an old
    report asked, no loss separates -- 2 worse / 0 better is p=0.5 -- and 5
    of 6 lost between two new reports is p=0.062: both read "no regression
    detected" for a lane whose kernels broke. Integrity items are the ones
    every working model answers, so losing half is a verdict, not a draw.
    """

    def test_a_collapse_against_an_old_report_is_a_regression(self):
        old = normalise(speed(llama_block()))
        new = normalise(speed(probe_block(wrong=EVERY_INTEGRITY)))
        findings, regressed = compare(old, new)
        assert regressed
        assert any("OK -> BROKEN" in f and "REGRESSION" in f for f in findings)

    def test_five_of_six_lost_between_new_reports_is_one(self):
        old = normalise(speed(probe_block()))
        new = normalise(speed(probe_block(wrong=EVERY_INTEGRITY - {"391"})))
        _, regressed = compare(old, new)
        assert regressed

    def test_one_integrity_miss_is_named_not_judged(self):
        # DEGRADED: the absolute gates fail it (--correctness-only exits 1,
        # upgrade_check's speed step); here it is one draw per case.
        old = normalise(speed(probe_block()))
        new = normalise(speed(probe_block(wrong={"391"})))
        findings, regressed = compare(old, new)
        assert not regressed
        assert any("flipped" in f and MULTIPLY in f for f in findings)

    def test_a_lane_broken_on_both_sides_is_no_new_regression(self):
        old = normalise(speed(probe_block(wrong=EVERY_INTEGRITY)))
        new = normalise(speed(probe_block(wrong=EVERY_INTEGRITY)))
        _, regressed = compare(old, new)
        assert not regressed
