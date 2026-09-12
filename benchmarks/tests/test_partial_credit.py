"""Tests for per-assertion partial credit and the environment record.

Pass/fail cannot distinguish "wrong algorithm" from "one edge case missed", and
running the test block as a whole stopped at the FIRST failure — throwing away
most of the signal the assertions carry.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_coding import NOVEL_TASKS, TASKS, run_candidate  # noqa: E402

MERGE = next(t for t in TASKS if t["name"] == "merge_sorted")

CORRECT = """
def merge_sorted(a, b):
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out
"""

# Sorts correctly but drops duplicates: fails exactly one assertion.
ALMOST = """
def merge_sorted(a, b):
    out = []
    for x in a + b:
        if x not in out:
            out.append(x)
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            if out[j] < out[i]:
                out[i], out[j] = out[j], out[i]
    return out
"""

USELESS = """
def merge_sorted(a, b):
    return []
"""


class TestPartialCredit:
    def test_a_correct_solution_scores_every_assertion(self):
        ok, _, credit = run_candidate(
            CORRECT, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        assert ok and credit["passed"] == credit["total"] > 0

    def test_one_missed_edge_case_is_not_the_same_as_total_failure(self):
        _, _, almost = run_candidate(
            ALMOST, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        _, _, useless = run_candidate(
            USELESS, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        assert almost["passed"] > useless["passed"], (
            "a near-miss and a stub used to be indistinguishable"
        )
        assert almost["passed"] == almost["total"] - 1

    def test_later_assertions_still_run_after_an_early_failure(self):
        # The whole point: the block used to stop at the first failure.
        _, _, credit = run_candidate(
            USELESS, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        assert credit["total"] == len(
            [
                ln
                for ln in MERGE["tests"].strip().splitlines()
                if ln.startswith("assert")
            ]
        )
        assert credit["passed"] > 0, "the empty-list cases should still pass"

    def test_the_detail_names_the_score(self):
        _, detail, _ = run_candidate(
            ALMOST, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        assert "assertions passed" in detail

    def test_a_syntax_error_yields_no_assertions_rather_than_a_crash(self):
        ok, detail, credit = run_candidate(
            "def merge_sorted(a, b) return a", MERGE["tests"]
        )
        assert not ok and credit["total"] == 0 and "Error" in detail

    def test_a_forbidden_construct_short_circuits_before_running(self):
        ok, detail, credit = run_candidate(
            "def merge_sorted(a,b): return sorted(a+b)",
            MERGE["tests"],
            forbidden=MERGE.get("forbidden"),
        )
        assert not ok and "forbids" in detail and credit["total"] == 0

    def test_every_assertion_is_reported_individually(self):
        _, _, credit = run_candidate(
            ALMOST, MERGE["tests"], forbidden=MERGE.get("forbidden")
        )
        assert len(credit["assertions"]) == credit["total"]
        assert sum(1 for a in credit["assertions"] if a["passed"]) == credit["passed"]


class TestEnvironmentRecord:
    def test_live_lanes_are_recorded(self):
        # Results shift with what else is running: a CPU lane measured 23.7
        # tok/s alone and 18.6 next to a busy NPU lane.
        from orchestrant.benchmark.provenance import collect

        p = collect()
        assert "live_lanes" in p

    def test_differing_lanes_are_flagged_when_comparing(self):
        from orchestrant.benchmark.provenance import collect, compare

        old = collect(extra={"live_lanes": ["geniex-npu"]})
        new = collect(extra={"live_lanes": ["geniex-npu", "geniex-cpu"]})
        assert any("lanes were live" in n for n in compare(old, new))

    def test_an_unreachable_registry_does_not_raise(self):
        from orchestrant.benchmark.provenance import busy_lanes

        assert busy_lanes("/nonexistent/backends.json") in (None, [])


class TestAssertionGrouping:
    """The harness splits the test block into statements so one failure does
    not hide the rest. Getting that split wrong marks correct solutions as
    failing — which is what happened when `except` was separated from its
    `try`, producing a SyntaxError in the generated program."""

    def test_try_except_stays_one_statement(self):
        from bench_coding import _assertion_harness

        tests = (
            "assert f(1) == 1\n"
            "try:\n"
            '    f(None); raise AssertionError("should have raised")\n'
            "except ValueError:\n"
            "    pass\n"
        )
        harness, _ = _assertion_harness(tests)
        compile("def f(x):\n    return x\n" + harness, "<t>", "exec")

    def test_each_top_level_assert_is_its_own_group(self):
        from bench_coding import _assertion_harness

        h, n = _assertion_harness("assert 1\nassert 2\nassert 3\n")
        assert n == 3
        assert h.count("_RESULTS.append((") == 6  # 3 pass + 3 except arms

    def test_else_and_finally_also_stay_attached(self):
        from bench_coding import _assertion_harness

        tests = (
            "try:\n    x = 1\nexcept Exception:\n    x = 2\nelse:\n"
            "    x = 3\nfinally:\n    pass\n"
        )
        compile(_assertion_harness(tests)[0], "<t>", "exec")

    def test_comments_do_not_become_their_own_group(self):
        from bench_coding import _assertion_harness

        h, n = _assertion_harness("# a comment\nassert 1\n")
        assert n == 1
        assert h.count("_RESULTS.append((") == 2


class TestShouldRaiseChecksAreAssertions:
    """D10/R7. `try: f(bad); raise AssertionError(...) except ValueError: pass`
    is a check, not setup. Counting it as setup published inflated near-miss
    fractions -- a candidate that missed only the ValueError rule was reported
    'test setup raised' beside a perfect N/N.
    """

    LANE = next(t for t in NOVEL_TASKS if t["name"] == "parse_lane_spec")

    # Correct except that it never rejects a head with no '=' before the marker.
    MISSES_ONE_RULE = (
        "def parse_lane(spec: str) -> tuple:\n"
        "    marker = ',model='\n"
        "    if marker not in spec:\n"
        "        raise ValueError('missing marker')\n"
        "    head, model = spec.split(marker, 1)\n"
        "    name, _, url = head.partition('=')\n"
        "    return (name.strip(), url.strip().rstrip('/'), model.strip())\n"
    )

    def test_a_should_raise_block_counts_as_exactly_one_assertion(self):
        from bench_coding import _assertion_harness

        tests = (
            'try:\n    f(None)\n    raise AssertionError("should have raised")\n'
            "except ValueError:\n    pass\n"
        )
        harness, n = _assertion_harness(tests)
        assert n == 1
        assert harness.count("_RESULTS.append((") == 2  # its pass and except arms

    def test_a_try_that_asserts_nothing_is_still_setup(self):
        # Counting every try would pad the denominator the other way.
        from bench_coding import _assertion_harness

        tests = "try:\n    import json\nexcept ImportError:\n    json = None\n"
        assert _assertion_harness(tests)[1] == 0

    def test_the_shipped_task_counts_its_two_should_raise_checks(self):
        from bench_coding import _assertion_harness

        counted = _assertion_harness(self.LANE["tests"])[1]
        plain = len(
            [ln for ln in self.LANE["tests"].splitlines() if ln.startswith("assert")]
        )
        assert counted == plain + 2, "the two try/except checks are assertions too"

    def test_missing_one_rule_scores_n_minus_one_not_a_setup_failure(self):
        ok, detail, credit = run_candidate(self.MISSES_ONE_RULE, self.LANE["tests"])
        assert not ok
        assert credit["setup_failures"] == [], "a failed check is not a broken harness"
        assert "test setup raised" not in detail
        assert (credit["passed"], credit["total"]) == (credit["total"] - 1, 6), credit

    def test_the_reference_still_scores_every_assertion(self):
        ok, detail, credit = run_candidate(self.LANE["reference"], self.LANE["tests"])
        assert ok, detail
        assert credit["passed"] == credit["total"] == 6
