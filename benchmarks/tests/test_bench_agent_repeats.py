"""bench_agent --repeats: fresh trials, per-task counts and pass^k (P7.4).

One trial per fixture cannot say "every time", and on the llama.cpp lanes the
trials are real draws. These tests pin the arithmetic and the plumbing; the
agent is a monkeypatched run_agent and the verdict a monkeypatched verify, so
nothing here starts opencode, contacts a server or needs pytest on PATH.
"""

import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_agent as ba
from orchestrant.benchmark import stats as bench_stats

FIX = "fix_failing_test"


class TestPassHatK:
    """pass^k = mean over tasks of C(c, k) / C(n, k)."""

    def test_the_agent_uses_the_shared_helper(self):
        # One estimator for every tool: bench_compare prints the same pass^k.
        assert ba.pass_hat_k is bench_stats.pass_hat_k

    def test_a_task_that_always_passes_is_one_at_every_k(self):
        cases = {"a": (3, 3), "b": (3, 3)}
        assert [bench_stats.pass_hat_k(cases, k) for k in (1, 2, 3)] == [1.0, 1.0, 1.0]

    def test_a_task_that_never_passes_is_zero_at_every_k(self):
        assert [bench_stats.pass_hat_k({"a": (0, 3)}, k) for k in (1, 2, 3)] == [
            0,
            0,
            0,
        ]

    def test_two_of_three(self):
        cases = {"a": (2, 3)}
        assert bench_stats.pass_hat_k(cases, 1) == pytest.approx(2 / 3)
        assert bench_stats.pass_hat_k(cases, 2) == pytest.approx(1 / 3)
        assert bench_stats.pass_hat_k(cases, 3) == 0

    def test_the_mean_runs_over_tasks_not_over_trials(self):
        # 4 of 6 trials passed, but a task that always passes and one that
        # passes a third of the time are not "67 % reliable" at k = 3.
        cases = {"steady": (3, 3), "flaky": (1, 3)}
        assert bench_stats.pass_hat_k(cases, 1) == pytest.approx((1 + 1 / 3) / 2)
        assert bench_stats.pass_hat_k(cases, 2) == pytest.approx(0.5)
        assert bench_stats.pass_hat_k(cases, 3) == pytest.approx(0.5)

    def test_a_task_with_fewer_than_k_trials_is_left_out_at_that_k(self):
        # Two trials say nothing about three in a row; counting that task as 0
        # would charge the model for a blocked trial.
        cases = {"short": (2, 2), "full": (1, 3)}
        assert bench_stats.pass_hat_k(cases, 2) == pytest.approx((1 + 0) / 2)
        assert bench_stats.pass_hat_k(cases, 3) == 0

    def test_no_task_reaching_k_is_none_not_zero(self):
        assert bench_stats.pass_hat_k({"a": (1, 1)}, 2) is None
        assert bench_stats.pass_hat_k({}, 1) is None
        assert bench_stats.pass_hat_k({"blocked": (0, 0)}, 1) is None

    @pytest.mark.parametrize("k", [0, -1])
    def test_k_below_one_is_refused(self, k):
        with pytest.raises(ValueError, match="at least 1"):
            bench_stats.pass_hat_k({"a": (1, 1)}, k)

    @pytest.mark.parametrize(
        ("c", "n"), [(c, n) for n in range(1, 6) for c in range(n + 1)]
    )
    def test_it_is_the_share_of_k_subsets_of_trials_that_all_passed(self, c, n):
        # The unbiased estimator, checked against the definition it estimates:
        # pick k of the n recorded trials, every way; how often did all pass?
        trials = [True] * c + [False] * (n - c)
        for k in range(1, n + 1):
            subsets = list(itertools.combinations(trials, k))
            share = sum(all(s) for s in subsets) / len(subsets)
            assert bench_stats.pass_hat_k({"t": (c, n)}, k) == pytest.approx(share)

    def test_it_never_rises_with_k(self):
        cases = {"a": (4, 5), "b": (2, 5), "c": (5, 5), "d": (0, 5)}
        values = [bench_stats.pass_hat_k(cases, k) for k in range(1, 6)]
        assert values == sorted(values, reverse=True)


def row(task, verdict, attempt=0):
    """A result row as run_task writes it; verdict is pass, fail or blocked."""
    return {
        "task": task,
        "passed": verdict == "pass",
        "blocked": verdict == "blocked",
        "attempt": attempt,
    }


class TestSummariseTrials:
    def test_per_task_counts_and_pass_hat_k(self):
        results = [
            row("a", "pass"),
            row("b", "fail"),
            row("a", "pass", attempt=1),
            row("b", "pass", attempt=1),
        ]
        s = ba.summarise_trials(results, 2)
        assert s["per_task"] == {
            "a": {"passes": 2, "attempts": 2},
            "b": {"passes": 1, "attempts": 2},
        }
        assert s["pass_hat_k"] == [
            {"k": 1, "value": 0.75, "tasks": 2},
            {"k": 2, "value": 0.5, "tasks": 2},
        ]

    def test_the_interval_is_stored_and_is_the_printed_one(self):
        results = [row("a", "pass"), row("a", "pass"), row("a", "fail")]
        low, high = bench_stats.wilson_interval(2, 3)
        assert ba.summarise_trials(results, 3)["wilson_95"] == [
            round(low, 4),
            round(high, 4),
        ]

    def test_nothing_measured_stores_no_interval(self):
        # format_score prints n/a for 0/0; [0, 1] in the JSON would read as data.
        s = ba.summarise_trials([row("a", "blocked")], 1)
        assert s["wilson_95"] is None
        assert s["per_task"] == {"a": {"passes": 0, "attempts": 0}}
        assert s["pass_hat_k"] == [{"k": 1, "value": None, "tasks": 0}]

    def test_a_blocked_trial_is_not_a_failed_attempt(self):
        results = [
            row("a", "pass"),
            row("a", "blocked", attempt=1),
            row("a", "pass", attempt=2),
        ]
        s = ba.summarise_trials(results, 3)
        assert s["per_task"]["a"] == {"passes": 2, "attempts": 2}
        assert [p["value"] for p in s["pass_hat_k"]] == [1.0, 1.0, None]
        assert [p["tasks"] for p in s["pass_hat_k"]] == [1, 1, 0]


def run_main(monkeypatch, tmp_path, fake_run_agent, argv):
    """main() with a stub agent and a stub verdict; returns the report.

    `verify` reads a SOLVED marker the fake agent may write, so the verdict of
    each trial is chosen by the test and no fixture test suite runs.
    """
    from orchestrant.benchmark import provenance as bench_provenance

    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text('{"provider": {"lane": {"options": {"baseURL": "http://l:1/v1"}}}}')
    real_data = tmp_path / "xdg"
    (real_data / "opencode").mkdir(parents=True)
    (real_data / "opencode" / "auth.json").write_text('{"k": 1}')
    monkeypatch.setenv("OPENCODE_CONFIG", str(cfg))
    monkeypatch.setenv("XDG_DATA_HOME", str(real_data))
    monkeypatch.setattr(ba, "OPENCODE", sys.executable)
    monkeypatch.setattr(ba, "opencode_version", lambda: "0.0-test")
    monkeypatch.setattr(ba, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        ba,
        "verify",
        lambda ws, task: (os.path.exists(os.path.join(ws, "SOLVED")), "stub"),
    )
    monkeypatch.setattr(bench_provenance, "busy_lanes", lambda: None)
    monkeypatch.setattr(bench_provenance, "_server_models", lambda url, timeout=5: None)
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["bench_agent.py", "--output", str(out), *argv])
    ba.main()
    with open(out) as f:
        return json.load(f)


class Agent:
    """A fake opencode: records what each trial saw, solves the chosen ones."""

    def __init__(self, solve=lambda n: True, blocked=()):
        """`solve(n)` decides trial n's verdict; trials in `blocked` overflow."""
        self.solve, self.blocked, self.seen = solve, set(blocked), []

    def __call__(self, workspace, model, prompt, timeout, env):
        n = len(self.seen)
        data, state = env["XDG_DATA_HOME"], env["XDG_STATE_HOME"]
        self.seen.append(
            {
                "workspace": workspace,
                "prompt": prompt,
                "data": data,
                "state": state,
                "data_files": sorted(os.listdir(os.path.join(data, "opencode"))),
                "state_files": sorted(os.listdir(state)),
            }
        )
        # What opencode leaves behind: a session and a recent-model entry.
        with open(os.path.join(data, "opencode", "session.db"), "w") as f:
            f.write(str(n))
        with open(os.path.join(state, "model.json"), "w") as f:
            f.write(str(n))
        if n in self.blocked:
            error = {"data": {"message": "Input prompt too long"}}
            return [{"type": "error", "error": error}], 1.0, False, ""
        if self.solve(n):
            open(os.path.join(workspace, "SOLVED"), "w").close()
        return [{"type": "step_start"}, {"type": "tool"}], 2.0, False, ""


class TestRepeatsPlumbing:
    def test_every_trial_is_a_row_with_its_attempt(self, monkeypatch, tmp_path):
        agent = Agent(solve=lambda n: n != 1)
        d = run_main(
            monkeypatch,
            tmp_path,
            agent,
            ["--model", "lane/m", "--task", FIX, "--repeats", "3"],
        )
        rep = d["reports"][0]
        assert [r["attempt"] for r in rep["results"]] == [0, 1, 2]
        assert [r["passed"] for r in rep["results"]] == [True, False, True]
        assert rep["passed"] == 2 and rep["total"] == 3
        assert rep["repeats"] == 3 and d["config"]["repeats"] == 3
        assert rep["tasks_run"] == 1 and rep["trials_run"] == 3

    def test_the_report_carries_per_task_counts_pass_hat_k_and_the_interval(
        self, monkeypatch, tmp_path
    ):
        agent = Agent(solve=lambda n: n != 1)
        rep = run_main(
            monkeypatch,
            tmp_path,
            agent,
            ["--model", "lane/m", "--task", FIX, "--repeats", "3"],
        )["reports"][0]
        assert rep["per_task"] == {FIX: {"passes": 2, "attempts": 3}}
        assert rep["pass_hat_k"] == [
            {"k": 1, "value": round(2 / 3, 4), "tasks": 1},
            {"k": 2, "value": round(1 / 3, 4), "tasks": 1},
            {"k": 3, "value": 0.0, "tasks": 1},
        ]
        low, high = bench_stats.wilson_interval(2, 3)
        assert rep["wilson_95"] == [round(low, 4), round(high, 4)]

    def test_each_trial_gets_a_fresh_workspace_and_opencode_home(
        self, monkeypatch, tmp_path
    ):
        agent = Agent()
        run_main(
            monkeypatch,
            tmp_path,
            agent,
            ["--model", "lane/m", "--task", FIX, "--repeats", "3"],
        )
        for key in ("workspace", "data", "state"):
            paths = [s[key] for s in agent.seen]
            assert len(set(paths)) == 3, f"{key} was shared between trials"
            # Windows cannot delete git's read-only objects with ignore_errors;
            # the harness runs on Linux, where the workspace goes too.
            if key != "workspace" or sys.platform != "win32":
                assert not any(os.path.exists(p) for p in paths), f"{key} kept"
        # Nothing an earlier trial wrote is there for a later one to find; the
        # credentials are, in every trial.
        for s in agent.seen:
            assert s["data_files"] == ["auth.json"]
            assert s["state_files"] == []
        assert all(s["state"] != os.environ.get("XDG_STATE_HOME") for s in agent.seen)

    def test_trials_run_round_robin_over_the_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "TASKS", ba.TASKS[:2])
        agent = Agent()
        rep = run_main(
            monkeypatch, tmp_path, agent, ["--model", "lane/m", "--repeats", "2"]
        )["reports"][0]
        a, b = (t["name"] for t in ba.TASKS)
        assert [(r["task"], r["attempt"]) for r in rep["results"]] == [
            (a, 0),
            (b, 0),
            (a, 1),
            (b, 1),
        ]
        prompts = [s["prompt"] for s in agent.seen]
        assert all(p != q for p, q in itertools.pairwise(prompts))

    def test_a_blocked_trial_leaves_the_attempts_not_the_task(
        self, monkeypatch, tmp_path
    ):
        agent = Agent(blocked={1})
        rep = run_main(
            monkeypatch,
            tmp_path,
            agent,
            ["--model", "lane/m", "--task", FIX, "--repeats", "3"],
        )["reports"][0]
        assert rep["blocked_on_context"] == 1 and rep["total"] == 2
        assert rep["per_task"] == {FIX: {"passes": 2, "attempts": 2}}
        assert [p["value"] for p in rep["pass_hat_k"]] == [1.0, 1.0, None]

    def test_the_default_is_one_trial_and_prints_no_pass_hat(
        self, monkeypatch, tmp_path, capsys
    ):
        rep = run_main(
            monkeypatch, tmp_path, Agent(), ["--model", "lane/m", "--task", FIX]
        )["reports"][0]
        assert [r["attempt"] for r in rep["results"]] == [0]
        assert rep["repeats"] == 1 and len(rep["pass_hat_k"]) == 1
        out = capsys.readouterr().out
        assert "pass^" not in out and "attempted tasks completed" in out

    def test_repeats_print_the_per_task_table_and_pass_hat(
        self, monkeypatch, tmp_path, capsys
    ):
        run_main(
            monkeypatch,
            tmp_path,
            Agent(solve=lambda n: n == 0),
            ["--model", "lane/m", "--task", FIX, "--repeats", "2"],
        )
        out = capsys.readouterr().out
        assert "[1/2]" in out and "[2/2]" in out
        assert f"per task: {FIX} 1/2" in out
        assert "pass^1 50%  pass^2 0%" in out
        assert "1/2 = 50%" in out and "attempted trials completed" in out

    @pytest.mark.parametrize(
        ("value", "said"),
        [("0", "at least 1"), ("-2", "at least 1"), ("two", "whole number")],
    )
    def test_a_repeat_count_below_one_is_refused(
        self, monkeypatch, capsys, value, said
    ):
        monkeypatch.setattr(sys, "argv", ["bench_agent.py", "--repeats", value])
        with pytest.raises(SystemExit) as e:
            ba.main()
        assert e.value.code == 2
        err = capsys.readouterr().err
        assert said in err and "_at_least_one" not in err

    def test_bench_compare_reads_repeats_as_attempts_of_one_case(
        self, monkeypatch, tmp_path
    ):
        # The consumer that matters: rows sharing a task must aggregate to one
        # case with (passes, attempts), not collapse to the last row.
        import bench_compare

        d = run_main(
            monkeypatch,
            tmp_path,
            Agent(solve=lambda n: n != 1),
            ["--model", "lane/m", "--task", FIX, "--repeats", "3"],
        )
        entry = bench_compare.normalise(d)["entries"][0]
        assert entry["cases"] == {FIX: (2, 3)}


class TestOpencodeState:
    def test_state_lands_in_the_scratch_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "real-state"))
        env = ba.opencode_env(str(tmp_path / "scratch"), None)
        assert env["XDG_STATE_HOME"] == str(tmp_path / "scratch" / "state")
        assert os.path.isdir(env["XDG_STATE_HOME"])

    def test_config_and_cache_are_left_alone(self, monkeypatch, tmp_path):
        # They hold the installed plugin, models.json and rg: a fresh copy per
        # trial would be fetched from the network again.
        monkeypatch.setenv("XDG_CONFIG_HOME", "/real/config")
        monkeypatch.setenv("XDG_CACHE_HOME", "/real/cache")
        env = ba.opencode_env(str(tmp_path / "scratch"), None)
        assert env["XDG_CONFIG_HOME"] == "/real/config"
        assert env["XDG_CACHE_HOME"] == "/real/cache"
