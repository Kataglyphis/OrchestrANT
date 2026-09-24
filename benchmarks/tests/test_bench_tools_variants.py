"""P1.2, the open half: --prompt-variants reports the SPREAD, not only a sum.

A combined score over every phrasing cannot say whether a model understood a
case or matched its wording. The spread can: a case one phrasing passes and
another fails was decided by the wording. And the phrasings of one case are
correlated draws of THAT case, so they must never inflate `effective_n` -- the
old (case, variant) key counted a case asked three ways as three cases, which
narrows the printed interval by a factor nobody measured.

The helpers live in bench_variants.py, which bench_coding shares; its own
wiring is covered in test_bench_coding_variants.py (Linux only, like the rest
of bench_coding). Nothing here opens a socket: `call` and `call_multi` are
stubbed.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_tools as bt  # noqa: E402
import bench_variants as bv  # noqa: E402
from bench_compare import mark_suspect_cases  # noqa: E402

from orchestrant.benchmark import client as bench_cli  # noqa: E402
from orchestrant.benchmark import provenance  # noqa: E402


# Named outcomes for the rows below; the tables read better than True/False.
P, F = True, False


def _row(case, variant, passed, attempt=0, **extra):
    return {
        "case": case,
        "variant": variant,
        "attempt": attempt,
        "passed": passed,
        **extra,
    }


class TestTheSpreadDefinition:
    """Which cases count as 'decided by the wording', and which do not."""

    def test_pass_on_one_phrasing_and_fail_on_another_is_spread(self):
        rows = [_row("a", 0, P), _row("a", 1, F), _row("a", 2, P)]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["variant_spread"] == 1
        assert s["variant_spread_cases"] == ["a"]
        assert s["variant_outcomes"]["a"] == {
            "phrasings": [[1, 1], [0, 1], [1, 1]],
            "disagreed": True,
        }

    def test_agreeing_phrasings_are_not_spread_either_way(self):
        rows = [
            _row("pass", 0, P),
            _row("pass", 1, P),
            _row("fail", 0, F),
            _row("fail", 1, F),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["variant_spread"] == 0
        assert s["variant_case_count"] == 2
        assert s["variant_spread_rate"] == 0.0

    def test_a_flaky_draw_on_every_phrasing_is_noise_not_wording(self):
        # Sampling lane, two repeats: each phrasing passed once and failed once.
        # No phrasing is worse than another, so the wording decided nothing.
        rows = [
            _row("a", 0, P, attempt=0),
            _row("a", 1, F, attempt=0),
            _row("a", 0, F, attempt=1),
            _row("a", 1, P, attempt=1),
        ]
        assert bv.variant_spread(rows, "case", collapse=False)["variant_spread"] == 0

    def test_a_phrasing_that_never_passes_is_spread_on_a_sampling_lane(self):
        rows = [
            _row("a", 0, P, attempt=0),
            _row("a", 1, F, attempt=0),
            _row("a", 0, F, attempt=1),
            _row("a", 1, F, attempt=1),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["variant_spread"] == 1
        assert s["variant_outcomes"]["a"]["phrasings"] == [[1, 2], [0, 2]]

    def test_a_case_asked_one_way_cannot_disagree_and_is_not_in_the_rate(self):
        rows = [_row("a", 0, P), _row("a", 1, F), _row("b", 0, F)]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["variant_case_count"] == 1
        assert s["variant_spread_rate"] == 1.0
        assert "b" not in s["variant_outcomes"]

    def test_a_phrasing_with_no_measured_attempt_is_zero_of_zero(self):
        # v1 errored (so it is not in the measured rows) -- v0 and v2 decide.
        rows = [_row("a", 0, P), _row("a", 2, F)]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["variant_outcomes"]["a"]["phrasings"] == [[1, 1], [0, 0], [0, 1]]
        assert s["variant_spread"] == 1

    def test_no_variant_case_gives_no_rate_rather_than_zero(self):
        s = bv.variant_spread([_row("a", 0, P)], "case", collapse=False)
        assert s["variant_case_count"] == 0
        assert s["variant_spread_rate"] is None
        assert s["by_variant"] == []

    def test_the_task_key_works_for_bench_coding_rows(self):
        rows = [
            {"task": "t", "variant": 0, "attempt": 0, "passed": True},
            {"task": "t", "variant": 1, "attempt": 0, "passed": False},
        ]
        assert bv.variant_spread(rows, "task", collapse=False)["variant_spread"] == 1


class TestTheScorePerPhrasing:
    def test_each_phrasing_is_scored_over_the_cases_asked_more_than_one_way(self):
        # "b" has no paraphrase: counting it under v0 would compare v0 over
        # three cases with v1 over two -- two case sets, not two wordings.
        rows = [
            _row("a", 0, P),
            _row("a", 1, F),
            _row("c", 0, P),
            _row("c", 1, P),
            _row("c", 2, F),
            _row("b", 0, P),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["by_variant"] == [
            {"variant": 0, "passed": 2, "total": 2, "cases": 2},
            {"variant": 1, "passed": 1, "total": 2, "cases": 2},
            {"variant": 2, "passed": 0, "total": 1, "cases": 1},
        ]


class TestParaphrasesDoNotInflateTheSample:
    def test_one_draw_per_phrasing_is_one_observation_per_case(self):
        rows = [
            _row("a", 0, P),
            _row("a", 1, P),
            _row("a", 2, P),
            _row("b", 0, P),
            _row("b", 1, F),
            _row("c", 0, F),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert s["effective_n"] == 3, "three cases, not six attempts"
        # Observed as written: "b" passed v0; its failed paraphrase is spread.
        assert s["effective_k"] == 2
        assert s["variant_spread_cases"] == ["b"]

    def test_agreeing_repeats_collapse_to_the_case(self):
        rows = [_row("a", v, P, attempt=r) for v in range(2) for r in range(3)] + [
            _row("b", 0, F, attempt=r) for r in range(3)
        ]
        s = bv.variant_spread(rows, "case", collapse=True)
        assert (s["effective_n"], s["effective_k"]) == (2, 1)

    def test_disagreeing_repeats_keep_one_observation_per_round(self):
        # The existing rule for a sampling lane counts draws; paraphrases do
        # not add to it: a round (every phrasing once) is one observation.
        rows = [
            _row("a", 0, P, attempt=0),
            _row("a", 1, P, attempt=0),
            _row("a", 0, F, attempt=1),
            _row("a", 1, P, attempt=1),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert (s["effective_n"], s["effective_k"]) == (2, 1)

    def test_the_observation_is_the_prompt_as_written(self):
        # The unit a run without --prompt-variants would have counted, so the
        # two intervals stay comparable; the paraphrases feed the spread.
        rows = [_row("a", 0, F), _row("a", 1, P), _row("a", 2, P)]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert (s["effective_n"], s["effective_k"]) == (1, 0)

    def test_an_unmeasured_v0_is_observed_through_the_next_phrasing(self):
        # v0 errored in round 1 (not in the measured rows): the case was
        # still observed in that round, through v1.
        rows = [
            _row("a", 0, P, attempt=0),
            _row("a", 1, F, attempt=0),
            _row("a", 1, F, attempt=1),
            _row("a", 2, P, attempt=1),
        ]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert (s["effective_n"], s["effective_k"]) == (2, 1)

    def test_a_sampling_lane_is_not_charged_once_per_paraphrase(self):
        # No wording effect at all: every phrasing fails exactly one draw of
        # three, each in a different round. "Passed only in every phrasing"
        # scored this 0/3 -- the noise of three phrasings, not the model.
        rows = [_row("a", v, v != r, attempt=r) for v in range(3) for r in range(3)]
        s = bv.variant_spread(rows, "case", collapse=False)
        assert (s["effective_n"], s["effective_k"]) == (3, 2)
        assert s["variant_spread"] == 0


class TestPhrasingAgreement:
    def test_the_vote_is_per_phrasing_because_two_phrasings_are_two_prompts(self):
        rows = [
            _row("a", 0, P, attempt=0, h="x"),
            _row("a", 0, P, attempt=1, h="x"),
            _row("a", 1, F, attempt=0, h="y"),
            _row("a", 1, F, attempt=1, h="y"),
        ]
        assert bv.phrasing_agreement(rows, "case", 2, "h") == (True, True)

    def test_differing_text_with_the_same_verdict_agrees_but_is_not_deterministic(
        self,
    ):
        rows = [
            _row("a", 0, P, attempt=0, h="x"),
            _row("a", 0, P, attempt=1, h="z"),
        ]
        assert bv.phrasing_agreement(rows, "case", 2, "h") == (False, True)

    def test_one_draw_is_never_called_deterministic(self):
        assert bv.phrasing_agreement([_row("a", 0, P, h="x")], "case", 1, "h") == (
            False,
            False,
        )


class TestThePrintedLines:
    def test_the_spread_the_per_phrasing_score_and_the_sample_are_printed(self):
        rows = [_row("a", 0, P), _row("a", 1, F), _row("b", 0, P)]
        lines = bv.variant_spread_lines(
            bv.variant_spread(rows, "case", collapse=False), total=3
        )
        assert (
            "1/1 cases passed in one phrasing and failed in another (100%): a"
            in (lines[0])
        )
        assert "v0 1/1, v1 0/1" in lines[1]
        assert "effective sample 2, not 3 attempts (2 passed as written)" in (lines[2])

    def test_one_draw_per_phrasing_says_the_spread_may_be_the_sampler(self):
        rows = [_row("a", 0, P), _row("a", 1, F)]
        summary = bv.variant_spread(rows, "case", collapse=False)
        one = bv.variant_spread_lines(summary, total=2)
        assert "an unlucky draw reads as spread too" in one[-1]
        three = bv.variant_spread_lines(summary, total=6, repeats=3)
        assert len(three) == len(one) - 1
        # No spread, nothing to explain.
        rows = [_row("a", 0, P), _row("a", 1, P)]
        agreed = bv.variant_spread(rows, "case", collapse=False)
        assert len(bv.variant_spread_lines(agreed, total=2)) == 3

    def test_a_suite_without_paraphrases_says_so(self):
        lines = bv.variant_spread_lines(
            bv.variant_spread([_row("a", 0, P)], "case", collapse=False), total=1
        )
        assert lines == [
            "       prompt variants: no case was measured in two phrasings"
        ]

    def test_the_unit_is_named_by_the_caller(self):
        rows = [
            {"task": "t", "variant": 0, "passed": True},
            {"task": "t", "variant": 1, "passed": True},
        ]
        lines = bv.variant_spread_lines(
            bv.variant_spread(rows, "task", collapse=False), total=2, unit="task"
        )
        assert "0/1 tasks passed" in lines[0]


# ── evaluate() end to end, with the request path stubbed ─────────────────────


def _case(name, paraphrases=0):
    return {
        "name": name,
        "category": "lookup",
        "prompt": f"do {name}",
        "variants": [f"please {name} v{i}" for i in range(1, paraphrases + 1)],
        "expect": {"name": "read_file", "args": {"path": f"{name}.txt"}},
    }


def _call_for(name, passing=True, content=None):
    path = f"{name}.txt" if passing else "wrong.txt"
    return {
        "content": content,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": path}),
                },
            }
        ],
    }


def _case_of(prompt):
    """'do a' / 'please a v1' -> 'a'."""
    return prompt.split()[1] if prompt.startswith("please") else prompt.split()[-1]


@pytest.fixture
def suite(monkeypatch):
    """'a' has two paraphrases, 'b' one, 'c' none; no multi-turn cases."""
    cases = [_case("a", 2), _case("b", 1), _case("c")]
    monkeypatch.setattr(bt, "CASES", cases)
    monkeypatch.setattr(bt, "MULTI_CASES", [])
    return cases


def _stub(monkeypatch, verdict):
    """verdict(case, prompt, n) -> passed? ; n counts requests per prompt."""
    seen, sent = {}, []

    def call(base_url, model, prompt, system=None, tools=None, entry=None):
        seen[prompt] = seen.get(prompt, 0) + 1
        sent.append(prompt)
        name = _case_of(prompt)
        return _call_for(name, verdict(name, prompt, seen[prompt])), "stop", 1.0

    monkeypatch.setattr(bt, "call", call)
    return sent


class TestEvaluateReportsTheSpread:
    def test_the_row_carries_the_spread_and_the_score_per_phrasing(
        self, monkeypatch, suite, capsys
    ):
        # 'b' passes as written and fails its paraphrase; 'a' and 'c' pass.
        _stub(monkeypatch, lambda name, prompt, n: not prompt.startswith("please b"))
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, prompt_variants=True)
        assert (row["passed"], row["total"]) == (5, 6)
        assert row["variant_case_count"] == 2
        assert row["variant_spread"] == 1 and row["variant_spread_cases"] == ["b"]
        assert row["variant_spread_rate"] == 0.5
        assert row["by_variant"][0] == {
            "variant": 0,
            "passed": 2,
            "total": 2,
            "cases": 2,
        }
        assert row["variant_outcomes"]["b"]["phrasings"] == [[1, 1], [0, 1]]
        out = capsys.readouterr().out
        assert "prompt variants: 1/2 cases passed in one phrasing" in out
        assert "per phrasing (v0 = as written)" in out

    def test_paraphrases_do_not_inflate_effective_n(self, monkeypatch, suite):
        _stub(monkeypatch, lambda name, prompt, n: True)
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, prompt_variants=True)
        assert row["total"] == 6, "every phrasing is still an attempt"
        # Counting attempts made this 6: three cases asked six ways.
        assert (row["effective_n"], row["effective_k"]) == (3, 3)

    def test_the_effective_count_observes_each_case_as_written(
        self, monkeypatch, suite
    ):
        # 'a' fails a paraphrase (spread); 'b' fails as written (a miss).
        _stub(
            monkeypatch,
            lambda name, prompt, n: prompt not in ("please a v2", "do b"),
        )
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, prompt_variants=True)
        assert (row["effective_n"], row["effective_k"]) == (3, 2)
        assert row["variant_spread_cases"] == ["a", "b"]

    def test_deterministic_repeats_collapse_to_cases_not_to_case_variant_pairs(
        self, monkeypatch, suite
    ):
        _stub(monkeypatch, lambda name, prompt, n: True)
        row = bt.evaluate(
            "http://x", "m", "lbl", repeats=3, warmup=False, prompt_variants=True
        )
        assert row["deterministic"] is True
        assert row["total"] == 18
        assert row["effective_n"] == 3, "was 6: one per (case, variant)"

    def test_sampling_repeats_count_rounds_not_phrasings(self, monkeypatch, suite):
        # 'a' fails as written on its first draw only: the repeats disagree, so
        # the sample is draws -- but a draw of 'a' is one round of its phrasings.
        _stub(
            monkeypatch,
            lambda name, prompt, n: not (prompt == "do a" and n == 1),
        )
        row = bt.evaluate(
            "http://x", "m", "lbl", repeats=2, warmup=False, prompt_variants=True
        )
        assert row["repeats_agreed"] is False
        assert row["total"] == 12
        # Three cases x two rounds; round 1 of 'a' failed as written.
        assert (row["effective_n"], row["effective_k"]) == (6, 5)
        # One flaky draw on v0, which passed the other: noise, not wording.
        assert row["variant_spread"] == 0

    def test_without_the_flag_nothing_changes(self, monkeypatch, suite):
        sent = _stub(monkeypatch, lambda name, prompt, n: True)
        row = bt.evaluate("http://x", "m", "lbl", warmup=False)
        assert sent == ["do a", "do b", "do c"]
        assert row["prompt_variants"] is False
        assert not set(bv.VARIANT_FIELDS) & set(row)
        assert (row["effective_n"], row["effective_k"]) == (3, 3)

    def test_a_flag_with_no_paraphrase_to_ask_keeps_the_old_sample(self, monkeypatch):
        monkeypatch.setattr(bt, "CASES", [_case("a"), _case("b")])
        monkeypatch.setattr(bt, "MULTI_CASES", [])
        _stub(monkeypatch, lambda name, prompt, n: name == "a")
        row = bt.evaluate("http://x", "m", "lbl", warmup=False, prompt_variants=True)
        assert row["variant_case_count"] == 0
        assert (row["effective_n"], row["effective_k"]) == (2, 1)

    def test_phrasings_are_never_identical_follow_ups(
        self, monkeypatch, suite, spacers
    ):
        sent = _stub(monkeypatch, lambda name, prompt, n: True)
        bt.evaluate(
            "http://x", "m", "lbl", repeats=2, warmup=False, prompt_variants=True
        )
        # 'a' and 'b' alternate their phrasings; only 'c' repeats itself.
        assert sent[:6] == ["do a", "please a v1", "please a v2"] * 2
        assert len(spacers) == 1


class TestTheControlLeavesTheSampleInOnePass:
    """mark_suspect_cases re-derives the sample through the variant spread. It
    used to recount per (case, variant) and need bench_tools.rescore_variants
    straight after it, or a run with a control published every phrasing as
    its own case after all."""

    def _reports(self):
        control_rows = [
            _row("ok", 0, P),
            _row("ok", 1, P),
            _row("broken", 0, F),
            _row("broken", 1, F),
        ]
        lane_rows = [
            _row("ok", 0, P),
            _row("ok", 1, P),
            _row("broken", 0, P),
            _row("broken", 1, F),
            _row("solo", 0, P),
        ]
        reports = []
        for label, backend, rows in (
            ("control", "control", control_rows),
            ("lane", "geniex", lane_rows),
        ):
            s = bv.variant_spread(rows, "case", collapse=False)
            reports.append(
                {
                    "label": label,
                    "backend": backend,
                    "passed": sum(r["passed"] for r in rows),
                    "total": len(rows),
                    "deterministic": False,
                    "repeats_agreed": False,
                    "effective_n": s["effective_n"],
                    "effective_k": s["effective_k"],
                    "results": rows,
                    **bv.variant_report_fields(s),
                }
            )
        return reports

    def test_the_suspect_case_leaves_the_sample_and_the_spread(self):
        reports = self._reports()
        assert mark_suspect_cases(reports) == ["broken"]
        lane = reports[1]
        # The old recount said 3: both phrasings of 'ok', and 'solo'.
        assert (lane["effective_n"], lane["effective_k"]) == (2, 2)
        assert lane["variant_spread"] == 0 and lane["variant_case_count"] == 1
        assert (lane["passed"], lane["total"]) == (3, 3)

    def test_the_control_keeps_its_full_score(self):
        reports = self._reports()
        fields = ("passed", "total", "effective_n", "effective_k", *bv.VARIANT_FIELDS)
        before = copy.deepcopy({k: reports[0][k] for k in fields})
        mark_suspect_cases(reports)
        assert {k: reports[0][k] for k in fields} == before

    def test_a_row_without_variants_keeps_the_producers_rule(self):
        # A sampling lane without the flag counts every kept attempt.
        reports = self._reports()
        for report in reports:
            for field in bv.VARIANT_FIELDS:
                del report[field]
        mark_suspect_cases(reports)
        assert (reports[1]["effective_n"], reports[1]["effective_k"]) == (3, 3)


class TestMainWritesTheSpread:
    """The whole CLI path: --prompt-variants, a control, the written report."""

    CANDIDATES = [
        {
            "label": "control",
            "explicit_label": True,
            "backend": "control",
            "base_url": "http://control",
            "raw_base_url": None,
            "model": "ctl",
            "entry": {},
        },
        {
            "label": "lane",
            "explicit_label": True,
            "backend": "geniex",
            "base_url": "http://lane",
            "raw_base_url": None,
            "model": "q4",
            "entry": {},
        },
    ]

    def test_the_report_carries_the_spread_net_of_suspect_cases(
        self, monkeypatch, tmp_path, suite
    ):
        def call(base_url, model, prompt, system=None, tools=None, entry=None):
            name = _case_of(prompt)
            # The control fails 'c' (a broken case); the lane fails the
            # paraphrases of 'b' and passes everything else.
            if model == "ctl":
                passing = name != "c"
            else:
                passing = not prompt.startswith("please b")
            return _call_for(name, passing), "stop", 1.0

        monkeypatch.setattr(bt, "call", call)
        monkeypatch.setattr(
            bench_cli,
            "candidate_rows",
            lambda *a, **k: [dict(c) for c in self.CANDIDATES],
        )
        # Provenance asks each base_url for its models; nothing is listening.
        monkeypatch.setattr(bt, "_determinism_extra", lambda candidates: {})
        monkeypatch.setattr(provenance, "collect", lambda *a, **k: {})
        out = tmp_path / "tools.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bench_tools.py",
                "--prompt-variants",
                "--no-warmup",
                "--output",
                str(out),
            ],
        )
        bt.main()
        report = json.loads(out.read_text())
        assert report["config"]["prompt_variants"] is True
        lane = next(r for r in report["reports"] if r["label"] == "lane")
        assert lane["suspect_cases"] == ["c"]
        # 'b' passed as written; its failed paraphrase is the spread below.
        assert (lane["effective_n"], lane["effective_k"]) == (2, 2)
        assert lane["variant_spread_cases"] == ["b"]
        assert lane["variant_case_count"] == 2
