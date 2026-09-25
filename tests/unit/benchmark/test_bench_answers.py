"""Tests for what a reply is: thinking vs answer, finished vs cut.

Pinned against the case that published wrong numbers on 2026-09-24: a
thinking model whose `<think>` never closed inside max_tokens scored 0 %
thinking, the summary averaged only the rows that did close (86 % where the
run was ~95 %), and time to the token cap was printed as time to a finished
answer.
"""

import io
import json
from pathlib import Path

import pytest

from orchestrant.benchmark.answers import (
    MIN_DECODE_WINDOW_S,
    accounting,
    decode_fields,
    from_body,
    read_stream,
    row_answer_s,
    row_decode_rate,
    row_thinking_share,
    row_thinking_unknown,
    split_answer,
    summary_lines,
    thinking_share,
)
from orchestrant.benchmark.client import utf8_stdio


ROADMAP_RUN = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "benchmark_results"
    / "2026-09-24-roadmap"
)


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
        assert acct["thinking_share_note"] is None

    def test_a_cut_reply_with_no_marker_records_no_share_and_why(self):
        # The speed runner reads a reply with the same rule as bench_coding: a
        # template that opened <think> in the prompt leaves this one no marker.
        reply = read_stream(_sse({"content": "Okay, the user wants"}, finish="length"))
        acct = accounting(reply)
        assert acct["thinking_char_share"] is None and acct["answered"] is False
        assert "<think>" in acct["thinking_share_note"]


class TestAShareTheReplyCannotSay:
    """Qwen3 and the Qwen3.8 distills open `<think>` in the PROMPT, so a reply
    carries only the closing tag, and one cut before it carries neither. The
    9B distill's two CUT rows of cpu-9b-classic-r3.json read 0 % thinking
    beside 42-96 % on the seven that finished. Such a reply may be all
    thinking and says nothing either way: its share is unknown, not 0.0.
    """

    def test_a_cut_reply_with_no_marker_and_no_reasoning_is_unknown(self):
        share, note = thinking_share("Let me parse the version string", cut=True)
        assert share is None and "</think>" in note

    def test_a_finished_reply_with_no_marker_is_still_no_thinking(self):
        # A template that opened <think> closes it before the answer.
        assert thinking_share("def parse_version(s):", cut=False) == (0.0, None)

    @pytest.mark.parametrize(
        ("content", "reasoning", "share"),
        [
            ("<think>still weighing", "", 1.0),  # opened by the reply itself
            ("plan</think>ANSWER", "", 0.667),  # opened by the template, closed
            ("ANSWER", "reasoning", 0.6),  # reasoning_content: no tag needed
        ],
    )
    def test_a_cut_reply_that_shows_its_thinking_keeps_its_share(
        self, content, reasoning, share
    ):
        assert thinking_share(content, reasoning, cut=True) == (share, None)

    def test_an_empty_reply_has_no_share_and_nothing_to_explain(self):
        assert thinking_share("", cut=True) == (None, None)


class TestAnOlderReportsShareOfACutReply:
    """A report written before `thinking_share_note` stored 0.0 for such a
    reply. At three decimals 0.0 means no marker and no reasoning, so a CUT
    row that reads 0.0 is read back as unknown and a finished one keeps it.
    """

    def test_the_9b_distills_cut_rows_read_as_unknown(self):
        with open(ROADMAP_RUN / "cpu-9b-classic-r3.json", encoding="utf-8") as f:
            rows = json.load(f)["reports"][0]["results"]
        cut = [row_thinking_share(r) for r in rows if r["truncated"]]
        done = [row_thinking_share(r) for r in rows if not r["truncated"]]
        assert cut == [None, None]
        assert done == [0.864, 0.902, 0.893, 0.416, 0.962, 0.93, 0.872]
        assert [row_thinking_unknown(r) for r in rows].count(True) == 2

    def test_a_speed_row_cut_at_the_cap_reads_as_unknown(self):
        row = {"answered": False, "finish_reason": "length", "thinking_char_share": 0.0}
        assert row_thinking_share(row) is None and row_thinking_unknown(row)

    def test_a_coding_row_that_passed_at_the_deadline_reads_as_unknown(self):
        # A pass is never `truncated`; `gave_up` still says the reply stopped
        # before the model did, as bench_coding's cut_off does for a new row.
        row = {"passed": True, "truncated": False, "gave_up": True}
        assert row_thinking_share({**row, "thinking_char_share": 0.0}) is None

    def test_a_finished_row_keeps_its_zero(self):
        for row in (
            {"answered": True, "finish_reason": "stop", "thinking_char_share": 0.0},
            # A blank reply is unanswered but was not cut.
            {"answered": False, "finish_reason": "stop", "thinking_char_share": 0.0},
            {"thinking_char_share": 0.0},  # older than `answered`: nothing says cut
        ):
            assert row_thinking_share(row) == 0.0 and not row_thinking_unknown(row)

    def test_a_row_that_says_reads_as_written(self):
        row = {"truncated": True, "thinking_char_share": 0.0}
        assert row_thinking_share({**row, "thinking_share_note": None}) == 0.0
        row = {"thinking_char_share": None, "thinking_share_note": "cut"}
        assert row_thinking_share(row) is None and row_thinking_unknown(row)
        assert not row_thinking_unknown({"thinking_char_share": None})


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


class TestDecodeFields:
    """A row's decode rate needs a window to time. Ollama sent three 8-12-token
    replies of ollama-t8-4b-instruct-speed-answer.json in one burst (latency ==
    TTFT): windows of 0.36-0.72 ms read 9,733-26,712 tok/s. Such a row keeps
    its window and says why it has no rate; the shortest real window in the
    tracked speed reports, 174 ms, keeps its rate.
    """

    def test_a_burst_has_no_rate_and_says_why(self):
        # Row 1 of the t8 run: 12 tokens, the 11 after the first in 0.41 ms.
        fields = decode_fields(2.58041, 2.58, 12)
        assert fields["decode_tok_per_sec"] is None
        note = "decode window 0.4 ms, under the 50 ms floor"
        assert fields["decode_rate_note"] == note
        assert fields["decode_s"] == pytest.approx(0.00041)

    def test_the_shortest_real_windows_keep_their_rates(self):
        # cpu-llama3b: 7 tokens in 0.174 s. v070r2-npu: 8 tokens in 0.309 s.
        for elapsed, ttft, tokens, rate in (
            (0.44, 0.26634, 7, 34.55),
            (0.46, 0.15068, 8, 22.63),
        ):
            fields = decode_fields(elapsed, ttft, tokens)
            assert fields["decode_tok_per_sec"] == rate
            assert fields["decode_rate_note"] is None

    def test_the_floor_lies_between_the_burst_and_the_real_windows(self):
        assert 0.00072 < MIN_DECODE_WINDOW_S < 0.174

    def test_a_one_token_reply_decoded_nothing(self):
        # The prefill produced its only token, as before: no rate, now a reason.
        fields = decode_fields(0.5, 0.2, 1)
        assert fields["decode_tok_per_sec"] is None
        note = "under 2 tokens: none decoded after the first"
        assert fields["decode_rate_note"] == note

    def test_an_unstreamed_reply_has_no_window(self):
        # Without --stream there is no first-token moment to start one.
        assert decode_fields(5.0, None, 100) == {
            "decode_s": None,
            "decode_tok_per_sec": None,
            "decode_rate_note": None,
        }


class TestRowDecodeRate:
    """A reader's decode rate withholds the one a report older than
    decode_fields stored from a burst: the tracked t8 run's rows 0-2 still
    read 9,733-26,712 tok/s, and paired against them a rerun that lost 20 %
    on the other six prompts read "noise +/-40%" and passed. Such a row's
    window is read back as (completion_tokens - 1) / rate.
    """

    def test_an_older_reports_burst_has_no_rate(self):
        # Row 1 of the t8 run: 12 tokens stored at 26,712 tok/s, 0.41 ms.
        burst = {"completion_tokens": 12, "decode_tok_per_sec": 26712.0}
        assert row_decode_rate(burst) is None

    def test_an_older_reports_shortest_real_window_keeps_its_rate(self):
        # cpu-llama3b row 2: 7 tokens at 34.55 tok/s, 174 ms.
        real = {"completion_tokens": 7, "decode_tok_per_sec": 34.55}
        assert row_decode_rate(real) == 34.55

    def test_a_row_decode_fields_wrote_reads_as_written(self):
        for elapsed, ttft, tokens in ((2.58041, 2.58, 12), (0.44, 0.26634, 7)):
            row = {"completion_tokens": tokens, **decode_fields(elapsed, ttft, tokens)}
            assert row_decode_rate(row) == row["decode_tok_per_sec"]

    def test_a_rate_without_a_token_count_is_kept(self):
        # No window to read back, so nothing says the reply burst.
        assert row_decode_rate({"decode_tok_per_sec": 19.5}) == 19.5
        assert row_decode_rate({"completion_tokens": 9}) is None


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

    def test_the_unknown_shares_are_counted_not_averaged(self):
        # Averaging only the rows that show a share reads a run whose cut
        # replies were all thinking as the share of the ones that finished.
        unknown = {"answered": False, "thinking_char_share": None}
        rows = [
            {"answered": True, "wall_s_to_answer": 2.0, "thinking_char_share": 0.9},
            {**unknown, "thinking_share_note": "cut before any marker"},
            {**unknown, "thinking_char_share": 0.0, "finish_reason": "length"},
        ]
        line = next(x for x in summary_lines(rows) if "Thinking share" in x)
        assert "90% of output was thinking on the 1 reply that shows it" in line
        assert "2 cut before any <think> marker: unknown" in line

    def test_a_run_whose_every_share_is_unknown_says_so(self):
        rows = [{"answered": False, "thinking_share_note": "cut"}] * 3
        line = next(x for x in summary_lines(rows) if "Thinking share" in x)
        assert "unknown: 3 cut before any <think> marker" in line

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
