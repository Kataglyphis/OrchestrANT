"""Tests for what a reply is: thinking vs answer, finished vs cut.

Pinned against the case that published wrong numbers on 2026-09-24: a
thinking model whose `<think>` never closed inside max_tokens scored 0 %
thinking, the summary averaged only the rows that did close (86 % where the
run was ~95 %), and time to the token cap was printed as time to a finished
answer.
"""

import io
import json

from orchestrant.benchmark.answers import (
    accounting,
    from_body,
    read_stream,
    row_answer_s,
    row_thinking_share,
    split_answer,
    summary_lines,
)
from orchestrant.benchmark.client import utf8_stdio


def _sse(*deltas, finish="stop", usage=None):
    lines = [
        "data:" + json.dumps({"choices": [{"delta": d, "finish_reason": None}]})
        for d in deltas
    ]
    lines.append(
        "data:" + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]})
    )
    if usage:
        lines.append("data:" + json.dumps({"choices": [], "usage": usage}))
    lines.append("data: [DONE]")
    return lines


def _clock():
    t = iter(range(100))
    return lambda: float(next(t))


class TestSplitAnswer:
    def test_a_think_block_that_never_closed_is_all_thinking(self):
        thinking, answer = split_answer("<think>\nOkay, the user wants")
        assert answer == "" and thinking == len("<think>\nOkay, the user wants")

    def test_a_closed_block_leaves_the_answer(self):
        _, answer = split_answer("<think>hm</think>\n\nThe sea.")
        assert answer.strip() == "The sea."

    def test_separate_reasoning_counts_as_thinking(self):
        thinking, answer = split_answer("The sea.", reasoning="Let me think.")
        assert answer == "The sea." and thinking == len("Let me think.")


class TestAccounting:
    def test_cut_inside_think_is_all_thinking_and_unanswered(self):
        reply = read_stream(_sse({"content": "<think>\nOkay"}, finish="length"))
        acct = accounting(reply)
        assert acct["thinking_char_share"] == 1.0
        assert acct["answered"] is False and acct["finish_reason"] == "length"

    def test_an_answer_cut_at_the_cap_is_not_a_finished_answer(self):
        # Row 3 of the v0.7.0 CPU run: </think> closed, then cut at 256.
        reply = read_stream(
            _sse(
                {"content": "<think>x</think>"}, {"content": "The sea"}, finish="length"
            )
        )
        assert accounting(reply)["answered"] is False

    def test_no_finish_reason_at_the_budget_is_a_cut(self):
        reply = from_body({"choices": [{"message": {"content": "The sea"}}]})
        assert (
            accounting(reply, completion_tokens=256, max_tokens=256)["answered"]
            is False
        )
        assert (
            accounting(reply, completion_tokens=12, max_tokens=256)["answered"] is True
        )

    def test_a_plain_finished_answer(self):
        reply = read_stream(_sse({"content": "The sea."}))
        acct = accounting(reply)
        assert acct["answered"] is True and acct["thinking_char_share"] == 0.0


class TestReadStream:
    def test_first_answer_comes_after_the_thinking(self):
        reply = read_stream(
            _sse(
                {"content": "<think>"},
                {"content": "hm"},
                {"content": "</think>"},
                {"content": "\n\n"},
                {"content": "The"},
            ),
            clock=_clock(),
        )
        assert reply.first_token_at == 0.0  # prefill ends at the first token
        assert reply.first_answer_at == 4.0  # not at "</think>" or blank lines

    def test_reasoning_content_is_output_not_prefill(self):
        # llama-server/vLLM: 30 reasoning deltas used to count as time to
        # first token, inflating decode to 488 tok/s.
        reply = read_stream(
            _sse(
                {"reasoning_content": "a"}, {"reasoning_content": "b"}, {"content": "c"}
            ),
            clock=_clock(),
        )
        assert reply.first_token_at == 0.0 and reply.first_answer_at == 2.0
        assert reply.chunks == 3 and reply.reasoning == "ab"

    def test_usage_after_the_last_choice_is_kept(self):
        reply = read_stream(_sse({"content": "x"}, usage={"completion_tokens": 1}))
        assert reply.usage == {"completion_tokens": 1}


class TestSummary:
    def test_cut_rows_are_counted_and_left_out_of_time_to_answer(self):
        rows = [
            {"answered": True, "wall_s_to_answer": 2.0, "thinking_char_share": 0.9},
            {"answered": False, "wall_s_to_answer": None, "thinking_char_share": 1.0},
        ]
        lines = "\n".join(summary_lines(rows))
        assert "1/2 inside max_tokens" in lines and "1 cut" in lines
        assert "2.0s avg over the 1 answered" in lines
        assert "rank models by THIS" not in lines  # not while rows are cut
        assert "95% of output was thinking" in lines

    def test_an_older_report_s_unclosed_think_reads_as_all_thinking(self):
        old = {"thinking_char_share": 0.0, "content_preview": "<think>\nOkay"}
        assert row_thinking_share(old) == 1.0
        assert row_thinking_share({"thinking_char_share": 0.0}) == 0.0

    def test_answer_seconds_only_for_answered_rows(self):
        assert row_answer_s({"answered": False, "wall_s_to_answer": None}) is None
        assert row_answer_s({"answered": True, "wall_s_to_answer": 3.0}) == 3.0
        assert row_answer_s({"latency_s": 4.0}) == 4.0  # pre-LB3 report


class TestUtf8Stdio:
    def test_a_cp1252_stream_prints_arrows_instead_of_raising(self, monkeypatch):
        # A redirected Windows stdout is cp1252; '→' raised and the CLI exited
        # 1, the code bench_compare and `contract --diff` use for a verdict.
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        monkeypatch.setattr("sys.stdout", stream)
        monkeypatch.setattr(
            "sys.stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        )
        utf8_stdio()
        print("SERVING RUNTIME CHANGED — v0.6.1 → v0.7.0 ─")
        stream.flush()
        assert "→".encode() in raw.getvalue()


class TestAnswerCell:
    def test_a_cut_row_says_so_instead_of_none(self):
        from orchestrant.benchmark.answers import answer_cell

        assert answer_cell({"answered": False, "wall_s_to_answer": None}) == "cut"
        assert answer_cell({"answered": True, "wall_s_to_answer": 2.5}) == 2.5
