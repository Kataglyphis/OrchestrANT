"""A lab run keeps every finished row across a Ctrl-C, and one raising grader fails only its row, in every lane."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_agent  # noqa: E402
import bench_chat  # noqa: E402
import bench_coding  # noqa: E402
from orchestrant.benchmark import client  # noqa: E402


def kept_rows(rows):
    with open(rows.path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_a_coding_grader_that_raises_fails_that_row_and_the_next_task_is_graded(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(bench_coding, "TASKS", bench_coding.TASKS[:2])
    reply = "```python\ndef f():\n    return 1\n```"
    monkeypatch.setattr(
        bench_coding,
        "ask",
        lambda *a, **k: (reply, 0.1, 1.0, 5, 10, "", "stop", None, False),
    )
    calls = iter(
        [RuntimeError("an odd reply"), (False, "wrong", {"passed": 0, "total": 1})]
    )

    def run_candidate(*a, **k):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bench_coding, "run_candidate", run_candidate)
    rows = client.RowLog(str(tmp_path / "coding.json"))
    report = bench_coding.evaluate("http://x", "m", "lbl", 100, warmup=False, rows=rows)
    first, second = report["results"]
    assert not first["passed"] and "the grader raised RuntimeError" in first["detail"]
    assert second["detail"] == "wrong"
    assert [r["task"] for r in kept_rows(rows)] == [first["task"], second["task"]]


def test_a_chat_grader_that_raises_fails_that_row(monkeypatch):
    monkeypatch.setattr(bench_chat, "ask", lambda *a, **k: ({"choices": []}, 1.0))
    monkeypatch.setattr(
        bench_chat,
        "grade_reply",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("choices")),
    )
    row = bench_chat.run_case("http://x", "m", bench_chat.CASES[0], 0, 64)
    assert row["passed"] is False and not row["errored"]
    assert row["detail"].startswith("the grader raised KeyError")


def test_a_ctrl_c_in_the_agent_lane_keeps_the_finished_trials(monkeypatch, tmp_path):
    trials = iter([{"task": "a", "passed": True}, KeyboardInterrupt()])

    def run_trial(task, attempt, args, config_path):
        outcome = next(trials)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(bench_agent, "run_trial", run_trial)
    rows = client.RowLog(str(tmp_path / "agent.json"))
    args = SimpleNamespace(repeats=1)
    with pytest.raises(
        SystemExit,
        match=r"after 0/1 candidate\(s\): no report written; every finished row is in",
    ):
        client.run_candidates(
            ["lbl"],
            lambda name: bench_agent.run_trials(
                [{"name": "a"}, {"name": "b"}], args, None, rows.collect(name)
            ),
            rows,
        )
    assert kept_rows(rows) == [{"candidate": "lbl", "task": "a", "passed": True}]


def test_a_new_run_starts_a_fresh_side_file(tmp_path):
    output = str(tmp_path / "tools.json")
    client.RowLog(output).add("old", {"case": "x"})
    rows = client.RowLog(output)
    assert not os.path.exists(rows.path)
    assert client.RowLog(None).path is None
