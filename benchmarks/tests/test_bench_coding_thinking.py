"""A coding row's thinking share where the reply itself cannot give one.

Qwen3 and the Qwen3.8 distills open `<think>` in the PROMPT, so a reply
carries only the closing tag, and one cut before it carries neither. Such a
reply read as 0 % thinking: the 9B distill's two CUT rows of
benchmark_results/2026-09-24-roadmap/cpu-9b-classic-r3.json scored 0.0 beside
0.42-0.96 on the seven that finished. It may be all thinking; nothing in it
says. Nothing here opens a socket -- `ask` is stubbed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_coding as bc  # noqa: E402

MERGE = next(t for t in bc.TASKS if t["name"] == "merge_sorted")
GOOD = (
    "def merge_sorted(a, b):\n    out, i, j = [], 0, 0\n"
    "    while i < len(a) and j < len(b):\n"
    "        if a[i] <= b[j]:\n            out.append(a[i]); i += 1\n"
    "        else:\n            out.append(b[j]); j += 1\n"
    "    return out + a[i:] + b[j:]\n"
)
STUB = "def merge_sorted(a, b):\n    return []\n"
# How a cut 9B reply reads when the template, not the reply, opened <think>.
DRAFT = "Okay, so the version string has three parts, and a missing one is 0"


def _row(monkeypatch, text, finish="length", tokens=3000, gave_up=False):
    """The one result row of a one-task run whose reply is `text`."""
    monkeypatch.setattr(bc, "TASKS", [MERGE])
    reply = (text, 0.1, 1.0, tokens, 10, "", finish, tokens, gave_up)
    monkeypatch.setattr(bc, "ask", lambda *a, **k: reply)
    return bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)["results"][0]


class TestACutReplyWithNoMarkerHasNoShare:
    """Cut with no `<think>`, no `</think>` and no reasoning: unknown, not 0.0."""

    def test_the_row_records_null_and_says_why(self, monkeypatch):
        row = _row(monkeypatch, DRAFT)
        assert row["truncated"] is True and row["thinking_char_share"] is None
        assert "</think>" in row["thinking_share_note"]

    def test_the_log_line_prints_a_question_mark_not_zero(self, monkeypatch, capsys):
        _row(monkeypatch, DRAFT)
        assert "think=  ?%" in capsys.readouterr().out

    def test_a_deadline_is_a_cut_too(self, monkeypatch):
        row = _row(monkeypatch, DRAFT, finish=None, tokens=1200, gave_up=True)
        assert row["gave_up"] is True and row["thinking_char_share"] is None

    def test_a_reply_cut_at_the_budget_is_unknown_even_when_it_passed(
        self, monkeypatch
    ):
        # Graded PASS, so not `truncated`; the reply still stopped at the budget
        # before any marker, and the code may be a draft inside the thinking.
        row = _row(monkeypatch, "```python\n" + GOOD + "```")
        assert row["passed"] is True and row["truncated"] is False
        assert row["thinking_char_share"] is None


class TestAFinishedReplyWithNoMarkerIsNoThinking:
    """A template that opened `<think>` closes it before the answer, so a
    reply that FINISHED without a marker keeps 0.0."""

    def test_the_row_records_zero_and_no_note(self, monkeypatch, capsys):
        row = _row(monkeypatch, "```python\n" + STUB + "```", finish="stop", tokens=90)
        assert row["passed"] is False and row["truncated"] is False
        assert row["thinking_char_share"] == 0.0
        assert row["thinking_share_note"] is None
        assert "think=  0%" in capsys.readouterr().out
