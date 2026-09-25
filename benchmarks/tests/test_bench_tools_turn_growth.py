"""bench_tools --turn-growth: what a turn that failed records, and how the loop
answers a turn's calls.

Turn 9 of the 9B run (benchmark_results/2026-09-24-roadmap/cpu-9b-turn-growth.json)
recorded only "HTTP Error 400: Bad Request", the status line str(HTTPError)
gives, and its log only "ERROR HTTPError": why the server refused that turn is
unknowable. The loop that led there answered only a turn's first call. Nothing
here opens a socket -- `call_multi` is stubbed.
"""

import copy
import io
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_tools as bt  # noqa: E402


CALLED = {
    "content": None,
    "tool_calls": [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "grep", "arguments": "{}"},
        }
    ],
}
# llama.cpp's refusal of a prompt past n_ctx, the shape the 9B lane serves.
REFUSAL = (
    b'{"error":{"code":400,"message":"the request exceeds the available context '
    b'size, try increasing it","type":"exceed_context_size_error",'
    b'"n_prompt_tokens":4210,"n_ctx":4096}}'
)


def refused(body=REFUSAL, code=400):
    return urllib.error.HTTPError(
        "http://h/v1/chat/completions", code, "Bad Request", {}, io.BytesIO(body)
    )


def growth(monkeypatch, failure):
    """turn_growth over a stub that answers turn 1 and raises `failure` on turn 2."""
    turns = iter(range(100))

    def call_multi(base_url, model, history, system=None, tools=None, entry=None):
        if next(turns):
            raise failure
        return CALLED, "tool_calls", 1.0

    monkeypatch.setattr(bt, "call_multi", call_multi)
    return bt.turn_growth("http://h", "m", max_turns=5, result_tokens=10)


def turn_two(capsys):
    out = capsys.readouterr().out
    return next(line for line in out.splitlines() if line.startswith("    turn  2"))


class TestAFailedTurnSaysWhy:
    """An HTTP error adds its status and the first 500 characters of the body
    to the row and to the printed line. The status line stays in `error`, and
    nothing else a row records changes.
    """

    def test_the_row_keeps_the_status_line_and_adds_status_and_body(self, monkeypatch):
        rows = growth(monkeypatch, refused())
        assert rows[-1] == {
            "turn": 2,
            "error": "HTTP Error 400: Bad Request",
            "http_status": 400,
            "response_body": REFUSAL.decode(),
        }

    def test_the_printed_line_says_it_too(self, monkeypatch, capsys):
        growth(monkeypatch, refused())
        expected = f"    turn  2: ERROR HTTPError 400: {REFUSAL.decode()}"
        assert turn_two(capsys) == expected

    def test_the_body_is_cut_at_500_characters(self, monkeypatch, capsys):
        rows = growth(monkeypatch, refused(b"x" * 900, code=500))
        assert rows[-1]["response_body"] == "x" * 500
        assert turn_two(capsys) == "    turn  2: ERROR HTTPError 500: " + "x" * 500

    def test_a_multi_line_body_prints_on_one_line(self, monkeypatch, capsys):
        # An HTML error page: the report keeps it, the log line stays one line.
        page = b"<html>\n  <body>Bad Gateway</body>\n</html>\n"
        rows = growth(monkeypatch, refused(page, code=502))
        assert rows[-1]["response_body"] == page.decode()
        assert turn_two(capsys) == (
            "    turn  2: ERROR HTTPError 502: <html> <body>Bad Gateway</body> </html>"
        )

    def test_any_other_failure_records_what_it_always_did(self, monkeypatch, capsys):
        rows = growth(monkeypatch, TimeoutError("timed out"))
        assert rows[-1] == {"turn": 2, "error": "timed out"}
        assert turn_two(capsys) == "    turn  2: ERROR TimeoutError"

    def test_the_turns_before_it_are_unchanged(self, monkeypatch):
        rows = growth(monkeypatch, refused())
        assert len(rows) == 2
        assert set(rows[0]) == {
            "turn",
            "approx_context_tokens",
            "tool_call",
            "tool_call_count",
            "empty",
            "wall_s",
            "finish_reason",
        }


def _call(call_id, name):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


TWO_CALLS = {"content": None, "tool_calls": [_call("a", "grep"), _call("b", "read")]}
TEXT = {"content": "done", "tool_calls": []}
# A stand-in: what the 9B's lane said at turn 9 was never kept.
UNANSWERED = b'{"error":{"code":400,"message":"unanswered tool_call"}}'


def unanswered(history):
    """The calls a strict server refuses: an assistant turn's tool_call ids not
    followed, in the same order, by one tool message each."""
    for i, message in enumerate(history):
        ids = [c.get("id") for c in message.get("tool_calls") or []]
        answers = []
        for later in history[i + 1 :]:
            if later.get("role") != "tool":
                break
            answers.append(later.get("tool_call_id"))
        if answers != ids:
            return ids
    return []


def loop(monkeypatch, replies, strict=False, wall=1.0):
    """turn_growth over a stub serving `replies`, one per turn: its rows, and
    a copy of the history each request carried. `strict` refuses a request
    with an unanswered call the way a strict server would, with a 400."""
    sent, queue = [], iter(replies)

    def call_multi(base_url, model, history, system=None, tools=None, entry=None):
        sent.append(copy.deepcopy(history))
        if strict and unanswered(history):
            raise refused(UNANSWERED)
        reply = next(queue)
        return reply, "tool_calls" if reply["tool_calls"] else "stop", wall

    monkeypatch.setattr(bt, "call_multi", call_multi)
    rows = bt.turn_growth("http://h", "m", max_turns=len(replies), result_tokens=10)
    return rows, sent


class TestEveryCallOfATurnIsAnswered:
    """Every call a turn makes gets its own tool message, one per tool_call_id
    in call order, before the next user message. The loop answered only
    tool_calls[0], so a turn with two calls sent the next request an assistant
    call with no answer, which an OpenAI-compatible server may refuse with a
    400. The row records how many calls the turn made; a turn is still one
    request, and its wall_s that request's alone.
    """

    def test_two_calls_get_two_answers_in_call_order(self, monkeypatch):
        _, sent = loop(monkeypatch, [TWO_CALLS, CALLED])
        second = sent[1]
        assert [m["role"] for m in second] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "user",
        ]
        assert second[1]["tool_calls"] == TWO_CALLS["tool_calls"]
        assert [m["tool_call_id"] for m in second[2:4]] == ["a", "b"]

    def test_a_strict_server_refuses_no_turn(self, monkeypatch):
        rows, _ = loop(monkeypatch, [TWO_CALLS, TWO_CALLS, CALLED], strict=True)
        assert [r["turn"] for r in rows] == [1, 2, 3]
        assert not any("error" in r for r in rows)

    def test_the_strict_stub_refuses_what_the_old_loop_sent(self):
        # So "refuses no turn" above is not vacuous: the first call answered
        # and the second not, as the loop used to send it.
        history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, **TWO_CALLS},
            {"role": "tool", "tool_call_id": "a", "content": "..."},
            {"role": "user", "content": "next"},
        ]
        assert unanswered(history) == ["a", "b"]

    def test_the_row_records_how_many_calls_the_turn_made(self, monkeypatch):
        rows, _ = loop(monkeypatch, [TWO_CALLS, CALLED, TEXT])
        assert [r["tool_call_count"] for r in rows] == [2, 1, 0]
        assert [r["tool_call"] for r in rows] == [True, True, False]

    def test_the_line_says_how_many_and_a_single_call_reads_as_before(
        self, monkeypatch, capsys
    ):
        loop(monkeypatch, [TWO_CALLS, CALLED, TEXT])
        lines = [
            ln for ln in capsys.readouterr().out.splitlines() if "ctx tokens" in ln
        ]
        assert "ctx tokens  2 tool_calls  " in lines[0]
        assert "ctx tokens  tool_call  " in lines[1]
        assert "ctx tokens  text  " in lines[2]

    def test_a_call_without_an_id_gets_one_on_both_sides(self, monkeypatch):
        bare = [
            {k: v for k, v in c.items() if k != "id"} for c in TWO_CALLS["tool_calls"]
        ]
        _, sent = loop(monkeypatch, [{"content": None, "tool_calls": bare}, TEXT])
        echoed = [c["id"] for c in sent[1][1]["tool_calls"]]
        assert echoed == ["c1_1", "c1_2"]
        assert [m["tool_call_id"] for m in sent[1][2:4]] == echoed
        assert [c["function"] for c in sent[1][1]["tool_calls"]] == [
            c["function"] for c in bare
        ]

    def test_a_turn_is_still_one_request_timed_alone(self, monkeypatch):
        rows, sent = loop(monkeypatch, [TWO_CALLS, TWO_CALLS, TEXT], wall=2.5)
        assert len(sent) == len(rows) == 3
        assert [r["wall_s"] for r in rows] == [2.5, 2.5, 2.5]

    def test_every_answer_counts_in_the_context_estimate(self, monkeypatch):
        # The estimate is what it was -- content characters over 4 -- and the
        # second answer is content like the first.
        rows, sent = loop(monkeypatch, [TWO_CALLS, TEXT])
        chars = sum(len(m.get("content") or "") for m in sent[1])
        assert rows[1]["approx_context_tokens"] == chars // 4
        filler = bt.context_padding(10)
        assert [m["content"] for m in sent[1] if m["role"] == "tool"] == [filler] * 2
