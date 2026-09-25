"""bench_tools --turn-growth: what a turn that failed records.

Turn 9 of the 9B run (benchmark_results/2026-09-24-roadmap/cpu-9b-turn-growth.json)
recorded only "HTTP Error 400: Bad Request", the status line str(HTTPError)
gives, and its log only "ERROR HTTPError": why the server refused that turn is
unknowable. Nothing here opens a socket -- `call_multi` is stubbed.
"""

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
            "empty",
            "wall_s",
            "finish_reason",
        }
