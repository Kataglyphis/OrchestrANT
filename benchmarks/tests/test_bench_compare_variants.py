"""One owner for the sample of a --prompt-variants run: mark_suspect_cases.

Once a control's suspect cases leave a report, mark_suspect_cases() recounts
effective_n/k from the kept rows. It did so by the rule from before
paraphrases were one case -- per (case, variant) when the repeats agree, per
attempt otherwise -- so bench_tools and bench_coding each called
bench_tools.rescore_variants() straight after it to repair the number: two
owners of one figure, and every new caller had to know about the second. The
recount now goes through bench_variants.variant_spread() whenever a report
carries `variant_case_count`, and the producers call nothing after it.

The numbers pinned below are what mark_suspect_cases + rescore_variants
produced before the refactor, over the tracked 2026-09-23 reports. No lane has
been measured with --prompt-variants yet, so the variant runs are the tracked
--repeats 3 tool run read as phrasings: its three draws of a case stand in for
three phrasings asked once ("phrasings"), or for two phrasings asked in
round 0 and the prompt as written again in round 1 ("rounds"). Real outcomes,
an errored draw included, in the shapes the producers write.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_variants as bv  # noqa: E402
from bench_compare import mark_suspect_cases, measured  # noqa: E402

TRACKED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark_results",
    "2026-09-23-geniex-upgrade",
)
TOOLS_R3 = "v070-npu-tools-r3.json"
CODING = "v070-npu-coding.json"

# The control fails these: an always-failing case, a case the lane passed in
# one phrasing only, an always-passing case, and two of the coding tasks.
BROKEN = (
    "contents_not_names",
    "overwrite_not_patch",
    "use_listing",
    "parse_version",
    "merge_sorted",
)

# attempt in the tracked run -> (attempt, variant) in the derived one.
SHAPES = {
    "phrasings": {0: (0, 0), 1: (0, 1), 2: (0, 2)},
    "rounds": {0: (0, 0), 1: (0, 1), 2: (1, 0)},
}


def _reshape(row, key, shape):
    """A tracked row under the producer's key (`case` or `task`) and shape."""
    out = {k: v for k, v in row.items() if k not in ("case", "task")}
    attempt, variant = SHAPES[shape][row["attempt"]]
    out.update({key: row.get("case") or row.get("task")})
    out.update(attempt=attempt, variant=variant)
    return out


def _reports(name, key, shape, collapse):
    """[control, lane] as a producer hands them to mark_suspect_cases."""
    with open(os.path.join(TRACKED, name)) as f:
        lane = copy.deepcopy(json.load(f)["reports"][0])
    rows = lane["results"]
    lane["repeats_agreed"] = collapse
    if shape != "plain":
        rows = [_reshape(r, key, shape) for r in rows]
        # What evaluate() writes under --prompt-variants.
        summary = bv.variant_spread([r for r in rows if measured(r)], key, collapse)
        lane.update(
            results=rows, deterministic=False, **bv.variant_report_fields(summary)
        )
        lane["effective_n"] = summary["effective_n"]
        lane["effective_k"] = summary["effective_k"]
    names = sorted({r.get("case") or r.get("task") for r in rows})
    control = {
        "label": "control",
        "backend": "control",
        "results": [{key: n, "passed": n not in BROKEN} for n in names],
    }
    return [control, lane]


class TestTheVariantSampleHasOneOwner:
    """mark_suspect_cases alone gives what it and rescore_variants gave."""

    @pytest.mark.parametrize("key", ["case", "task"])
    def test_three_phrasings_asked_once(self, key):
        reports = _reports(TOOLS_R3, key, "phrasings", collapse=False)
        suspect = mark_suspect_cases(reports)
        lane = reports[1]
        assert suspect == ["contents_not_names", "overwrite_not_patch", "use_listing"]
        # The old rule counted 115 here: every measured phrasing of 39 cases.
        assert (lane["effective_n"], lane["effective_k"]) == (39, 32)
        assert (lane["passed"], lane["total"]) == (94, 115)
        assert lane["variant_case_count"] == 38
        assert lane["variant_spread_cases"] == [
            "nested_path",
            "patch_not_overwrite",
            "recover_permission_denied",
            "what_changed_in_them",
        ]
        assert lane["by_variant"] == [
            {"variant": 0, "passed": 31, "total": 38, "cases": 38},
            {"variant": 1, "passed": 30, "total": 38, "cases": 38},
            {"variant": 2, "passed": 32, "total": 38, "cases": 38},
        ]

    @pytest.mark.parametrize(
        ("key", "collapse", "sample"),
        [
            ("case", False, (77, 64)),
            ("task", False, (77, 64)),
            ("case", True, (39, 31)),
        ],
    )
    def test_two_phrasings_over_two_rounds(self, key, collapse, sample):
        # A sampling lane counts a round of a case once; agreeing repeats
        # collapse to the case. Either way through the prompt as written.
        reports = _reports(TOOLS_R3, key, "rounds", collapse)
        mark_suspect_cases(reports)
        lane = reports[1]
        assert (lane["effective_n"], lane["effective_k"]) == sample
        assert lane["variant_spread"] == 3
        assert lane["by_variant"][0] == {
            "variant": 0,
            "passed": 63,
            "total": 76,
            "cases": 38,
        }

    def test_the_control_keeps_its_rows_and_is_not_rescored(self):
        reports = _reports(TOOLS_R3, "case", "phrasings", collapse=False)
        mark_suspect_cases(reports)
        control = reports[0]
        assert control["suspect_excluded"] == 0
        assert "effective_n" not in control and "variant_spread" not in control


class TestReportsWithoutVariantsKeepTheOldRule:
    """No `variant_case_count`: the producers' rule, per (case, variant)."""

    @pytest.mark.parametrize(
        ("name", "key", "collapse", "sample"),
        [
            (TOOLS_R3, "case", False, (115, 94)),
            (TOOLS_R3, "case", True, (39, 30)),
            (CODING, "task", False, (29, 18)),
        ],
    )
    def test_the_tracked_reports_recount_as_before(self, name, key, collapse, sample):
        reports = _reports(name, key, "plain", collapse)
        mark_suspect_cases(reports)
        lane = reports[1]
        assert (lane["effective_n"], lane["effective_k"]) == sample
        assert "variant_spread" not in lane

    def test_a_flag_with_no_paraphrase_keeps_the_old_rule(self):
        # variant_case_count 0: the run asked no case in two phrasings, and
        # evaluate() kept the producers' own sample.
        reports = _reports(TOOLS_R3, "case", "plain", collapse=False)
        reports[1]["variant_case_count"] = 0
        mark_suspect_cases(reports)
        assert (reports[1]["effective_n"], reports[1]["effective_k"]) == (115, 94)
