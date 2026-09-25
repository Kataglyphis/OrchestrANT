"""A tool call written as text is a call where a case wants none.

Llama-3.2-3B writes its calls as text JSON. Without --accept-text-json the
grader read such a reply as "no call": all seven restraint and irrelevance
cases of benchmark_results/2026-09-24-roadmap/cpu-llama3b-tools-r1.json
PASSED, and under the flag (cpu-llama3b-tools-textjson.json) every one of
them called a tool. A case that grades "no tool call" now reads the text with
the flag's own parser (_tool_calls_from_text, the shim's for Qwen's template)
either way, and its row says the call was written as text. Nothing here opens
a socket -- `call` and `call_multi` are stubbed.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_tools as bt  # noqa: E402

ROADMAP_RUN = (
    Path(__file__).resolve().parents[1] / "benchmark_results" / "2026-09-24-roadmap"
)
# no_tool_arithmetic's reply, byte-identical in both runs (message_sha256
# 091137fb...): the r1 run's run_the_tests reply is the same hash, and its
# detail quotes the first 60 characters; the rest was matched to the hash.
ARITHMETIC = (
    '{"name": "run_tests", "parameters": {"path": "None", "verbosity": "normal"}}'
)
# The r1 run's use_listing reply, whole in its detail (message_sha256 f1633b81...).
LISTING = '{"name": "list_files", "parameters": {"directory": "src/"}}'
# test_geniex_toolcall_shim.py's REAL_9B, verbatim: the 9B distill's call as
# GenieX v0.5 returned it in `content`, its <think> opened by the template.
REAL_9B = (
    "I need to first run the test suite to see what's failing, then examine "
    "the source code to find the bug.\n</think>\n\n"
    "<tool_call>\n"
    "<function=bash>\n"
    "<parameter=command>\n"
    "cd /tmp/tmp.Dr27GdIafy && python -m pytest test_calc.py -v 2>&1\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)
FOLLOW_UPS = [c["name"] for c in bt.MULTI_CASES if c["kind"] == "use_result"]


def _msg(text):
    return {"content": text, "tool_calls": []}


def _no_call_verdict(text, flag=False):
    """(ok, detail, recovered) of `text` in a case that wants no call."""
    return bt._grade(_msg(text), None, accept_text_json=flag, tools=bt.TOOLS)


def _rows(name):
    with open(ROADMAP_RUN / f"{name}.json", encoding="utf-8") as f:
        return {r["case"]: r for r in json.load(f)["reports"][0]["results"]}


class TestTheRepliesAreTheTrackedOnes:
    """The replies below are the ones the tracked reports graded: their
    message hashes are the rows' own."""

    def test_the_arithmetic_reply_passed_without_the_flag_and_failed_with_it(self):
        r1 = _rows("cpu-llama3b-tools-r1")["no_tool_arithmetic"]
        flag = _rows("cpu-llama3b-tools-textjson")["no_tool_arithmetic"]
        assert bt._message_hash(_msg(ARITHMETIC)) == r1["message_sha256"]
        assert flag["message_sha256"] == r1["message_sha256"]
        assert (r1["passed"], flag["passed"]) == (True, False)

    def test_the_listing_reply_is_the_r1_runs(self):
        r1 = _rows("cpu-llama3b-tools-r1")["use_listing"]
        assert bt._message_hash(_msg(LISTING)) == r1["message_sha256"]


class TestARestraintCaseFailsACallWrittenAsText:
    """Restraint and irrelevance are the single-turn cases that want no call."""

    def test_they_are_the_cases_that_expect_none(self):
        wanting_none = {c["category"] for c in bt.CASES if c["expect"] is None}
        assert wanting_none == {"restraint", "irrelevance"}

    @pytest.mark.parametrize("flag", [False, True])
    def test_the_tracked_reply_fails_with_or_without_the_flag(self, flag):
        ok, detail, recovered = _no_call_verdict(ARITHMETIC, flag)
        assert not ok and recovered
        assert detail.startswith("called run_tests (written as text) when none was")
        assert '{"name": "run_tests"' in detail  # the row shows what it read

    def test_the_verdict_does_not_depend_on_the_flag(self):
        assert _no_call_verdict(ARITHMETIC) == _no_call_verdict(ARITHMETIC, flag=True)

    def test_a_qwen_template_block_is_a_call_too(self):
        # The shim's parser reads it: a server that leaves the template in
        # `content` must not turn a call into restraint.
        text = "<tool_call>\n<function=git_status>\n</function>\n</tool_call>"
        ok, detail, _ = _no_call_verdict(text)
        assert not ok and "git_status" in detail

    @pytest.mark.parametrize(
        "text",
        ["2 + 2 = 4.", 'As JSON: {"sum": 4}', "A list is [1, 2]; a tuple is (1, 2)."],
    )
    def test_an_answer_that_names_no_call_still_passes(self, text):
        ok, detail, _ = _no_call_verdict(text)
        assert ok and detail == "correctly answered without a tool"


class TestAFollowUpFailsACallWrittenAsText:
    """use_result, long_result and deep_history want the tool's result used,
    and no further call (grade_followup): the same rule."""

    def test_they_are_the_cases_graded_by_grade_followup(self):
        graded = {c["category"] for c in bt.MULTI_CASES if c["name"] in FOLLOW_UPS}
        assert graded == {"use_result", "long_result", "deep_history"}

    def test_the_tracked_listing_reply_fails_on_its_call(self):
        ok, detail = bt.grade_followup(_msg(LISTING), ["setup.py"])
        assert not ok and "list_files" in detail and "as text" in detail

    def test_a_call_that_names_the_fact_is_not_an_answer(self):
        # use_search_hit wants "bench_tools.py", and a call to read it names it.
        text = '{"name": "read_file", "parameters": {"path": "bench_tools.py"}}'
        ok, detail = bt.grade_followup(_msg(text), ["bench_tools.py"])
        assert not ok and "as text" in detail

    def test_an_answer_from_the_result_still_passes(self):
        answer = _msg("It is in bench_tools.py, line 112.")
        assert bt.grade_followup(answer, ["bench_tools.py"])[0]


class TestTheCaseSuiteRowSaysSo:
    """Without the flag, the row of such a reply fails and names the text."""

    def _run(self, monkeypatch, cases, multi, reply):
        def answer(*_a, **_k):
            return _msg(reply), "stop", 7.0

        monkeypatch.setattr(bt, "CASES", cases)
        monkeypatch.setattr(bt, "MULTI_CASES", multi)
        monkeypatch.setattr(bt, "call", answer)
        monkeypatch.setattr(bt, "call_multi", answer)
        return bt.evaluate("http://x", "m", "lbl", warmup=False)

    def test_a_restraint_row(self, monkeypatch):
        case = next(c for c in bt.CASES if c["name"] == "no_tool_arithmetic")
        report = self._run(monkeypatch, [case], [], ARITHMETIC)
        (row,) = report["results"]
        assert (row["passed"], row["recovered"]) == (False, True)
        assert "as text" in row["detail"]
        # Not the flag's credit: "N recovered from text" counts passes only.
        assert report["recovered"] == 0

    @pytest.mark.parametrize("name", FOLLOW_UPS)
    def test_a_follow_up_row(self, monkeypatch, name):
        multi = next(c for c in bt.MULTI_CASES if c["name"] == name)
        (row,) = self._run(monkeypatch, [], [multi], LISTING)["results"]
        assert (row["passed"], row["recovered"]) == (False, True)
        assert "as text" in row["detail"]


class TestTheRowQuotesWhereTheCallWasRead:
    """A thinking model's reply opens with its thinking, and the parser reads
    a call only after `</think>`: the row quotes that text, so a reader can
    tell a real call from JSON the parser took for one."""

    CALL = REAL_9B.rsplit("</think>", 1)[-1].strip()

    def test_a_restraint_row(self):
        ok, detail, _ = _no_call_verdict(REAL_9B)
        assert not ok and detail.endswith(f"when none was needed: {self.CALL[:60]!r}")

    def test_a_follow_up_row(self):
        ok, detail = bt.grade_followup(_msg(REAL_9B), ["bench_tools.py"])
        assert not ok and detail.endswith(f"from the result: {self.CALL[:60]!r}")
