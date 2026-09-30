"""Tests for the sweep driver; every subprocess goes through the monkeypatched `run_step`."""

import json
import os
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from orchestrant.benchmark import client as bench_cli
import bench_chat  # noqa: E402
import bench_sweep  # noqa: E402


def cand(label, backend="npu", model="org/M", base_url="http://h:1", entry=None):
    return {
        "label": label,
        "backend": backend,
        "model": model,
        "base_url": base_url,
        "entry": entry or {},
    }


def sweep_args(outdir, **kw):
    base = {
        "outdir": outdir,
        "tools": ["coding"],
        "repeats": 1,
        "task_set": "all",
        "baseline": None,
        "title": None,
        "skip_gate": True,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def runner(monkeypatch):
    """Record every command the sweep would run; run none of them."""
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return fake.returncode

    fake.returncode = 0
    monkeypatch.setattr(bench_sweep, "run_step", fake)
    return calls


class TestSlug:
    def test_a_label_becomes_a_filename_fragment(self):
        assert bench_sweep.slug("Qwen3-4B Q4_0 (CPU lane)") == "qwen3-4b-q4-0-cpu-lane"

    def test_a_path_separator_cannot_survive(self):
        # A model id contains '/' and ':', which an --output path must not.
        assert "/" not in bench_sweep.slug("unsloth/Qwen3-4B-GGUF:Q4_0")

    def test_a_label_of_pure_punctuation_still_yields_a_name(self):
        assert bench_sweep.slug("///") == "unnamed"

    def test_the_path_is_tool_then_label(self, tmp_path):
        p = bench_sweep.output_path(str(tmp_path), "coding", "org/M")
        assert os.path.basename(p) == "coding_org-m.json"


class TestPlanRefusals:
    def test_one_path_per_tool_and_candidate(self, tmp_path):
        steps = bench_sweep.plan(
            [cand("a"), cand("b")], ["coding", "tools"], str(tmp_path)
        )
        assert len(steps) == 4
        assert len({s[2] for s in steps}) == 4

    def test_an_existing_file_is_never_overwritten(self, tmp_path):
        # An existing output is refused, never silently overwritten.
        (tmp_path / "coding_a.json").write_text("{}")
        with pytest.raises(SystemExit) as e:
            bench_sweep.plan([cand("a")], ["coding"], str(tmp_path))
        assert "coding_a.json" in str(e.value)

    def test_labels_that_slug_to_the_same_file_are_refused(self, tmp_path):
        # Disambiguated labels can still collide once punctuation is stripped.
        with pytest.raises(SystemExit) as e:
            bench_sweep.plan([cand("org/M"), cand("org M")], ["coding"], str(tmp_path))
        assert "org" in str(e.value)

    def test_the_refusal_happens_before_anything_runs(self, tmp_path, runner):
        (tmp_path / "coding_a.json").write_text("{}")
        with pytest.raises(SystemExit):
            bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path)))
        assert runner == []


class TestToolCommands:
    def _cmd(self, tool, **kw):
        return bench_sweep.tool_command(
            tool,
            cand("lbl", **kw),
            "/out/f.json",
            sweep_args("/out", repeats=3, task_set="all"),
        )

    def test_every_known_tool_builds_a_command(self, tmp_path):
        for tool in bench_sweep.TOOLS:
            cmd = self._cmd(tool)
            assert cmd[0] == sys.executable
            if cmd[1] == "-m":
                assert cmd[2] == "orchestrant.benchmark", tool
            else:
                assert cmd[1].endswith(".py"), tool
            assert "/out/f.json" in cmd

    def test_an_unknown_tool_is_refused(self):
        with pytest.raises(SystemExit):
            self._cmd("telepathy")

    def test_coding_gets_the_label_repeats_and_task_set(self):
        cmd = self._cmd("coding")
        assert cmd[cmd.index("--label") + 1] == "lbl"
        assert cmd[cmd.index("--repeats") + 1] == "3"
        assert cmd[cmd.index("--task-set") + 1] == "all"

    def test_coding_keeps_the_raw_replies(self):
        # No published coding number has a stored reply to re-audit.
        assert "--keep-output" in self._cmd("coding")

    def test_the_backend_name_is_passed_when_there_is_one(self):
        cmd = self._cmd("tools")
        assert cmd[cmd.index("--backend") + 1] == "npu"
        assert "--base-url" not in cmd

    def test_a_url_override_beside_a_backend_is_what_gets_measured(self):
        # Both flags: the URL wins, the entry keeps headers and keys.
        row = cand("lbl", base_url="http://override:7")
        row["raw_base_url"] = "http://override:7/"
        for tool in ("speed", "coding", "tools", "chat", "lanes"):
            cmd = bench_sweep.tool_command(
                tool, row, "/out/f.json", sweep_args("/out", repeats=1, task_set="all")
            )
            assert cmd[cmd.index("--backend") + 1] == "npu", tool
            assert cmd[cmd.index("--base-url") + 1] == "http://override:7", tool

    def test_an_explicit_url_is_used_when_there_is_no_backend(self):
        cmd = self._cmd("tools", backend=None, base_url="http://elsewhere:9")
        assert cmd[cmd.index("--base-url") + 1] == "http://elsewhere:9"

    def test_agent_gets_no_endpoint_flags(self):
        # opencode resolves its provider itself; --backend would be an argparse error.
        cmd = self._cmd("agent")
        assert "--backend" not in cmd and "--base-url" not in cmd

    def test_every_command_names_a_file_that_exists(self):
        for tool in bench_sweep.TOOLS:
            cmd = self._cmd(tool)
            if cmd[1] == "-m":
                # The runner tools are module entry points, not files.
                assert cmd[2] == "orchestrant.benchmark", tool
                continue
            assert os.path.exists(cmd[1]), tool

    def test_the_tools_command_is_unchanged_by_the_shared_builder(self):
        # Pinned token by token: audits compare _sweep.json argvs across runs.
        assert self._cmd("tools") == [
            sys.executable,
            os.path.join(bench_sweep.HERE, "bench_tools.py"),
            "--backend",
            "npu",
            "--model",
            "org/M",
            "--label",
            "lbl",
            "--repeats",
            "3",
            "--output",
            "/out/f.json",
        ]


class TestChatStep:
    """bench_chat is a sweep step like bench_tools: endpoint, label, repeats, derived output."""

    def _cmd(self, **kw):
        args = sweep_args("/out", repeats=3)
        return bench_sweep.tool_command("chat", cand("lbl", **kw), "/out/c.json", args)

    def test_chat_is_a_tool_the_sweep_accepts(self):
        assert bench_sweep.parse_tools("speed,chat") == ["speed", "chat"]

    def test_chat_gets_the_endpoint_label_repeats_and_output(self):
        cmd = self._cmd()
        assert cmd[1] == os.path.join(bench_sweep.HERE, "bench_chat.py")
        assert cmd[cmd.index("--backend") + 1] == "npu"
        assert cmd[cmd.index("--model") + 1] == "org/M"
        assert cmd[cmd.index("--label") + 1] == "lbl"
        assert cmd[cmd.index("--repeats") + 1] == "3"
        assert cmd[cmd.index("--output") + 1] == "/out/c.json"

    def test_an_explicit_url_is_used_when_there_is_no_backend(self):
        cmd = self._cmd(backend=None, base_url="http://elsewhere:9")
        assert cmd[cmd.index("--base-url") + 1] == "http://elsewhere:9"
        assert "--backend" not in cmd

    def test_chat_keeps_its_own_token_budget_and_every_category(self):
        # bench_chat's own budget and every category, or the report covers less than its name.
        cmd = self._cmd()
        assert "--max-tokens" not in cmd and "--category" not in cmd

    def test_every_flag_it_passes_is_one_bench_chat_accepts(self):
        # An unknown flag is an argparse exit 2 hours into the sweep.
        args = bench_chat.parse_args(self._cmd()[2:])
        assert (args.backend, args.label, args.repeats) == ("npu", "lbl", 3)
        assert args.output == "/out/c.json"

    def test_a_sweep_writes_chat_to_its_own_derived_file(self, tmp_path, runner):
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), tools=["chat"]))
        assert [x["status"] for x in s["steps"]] == ["ok"]
        assert s["steps"][0]["output"] == str(tmp_path / "chat_a.json")
        assert "bench_chat.py" in runner[0][1]

    def test_an_existing_chat_report_is_never_overwritten(self, tmp_path, runner):
        (tmp_path / "chat_a.json").write_text("{}")
        with pytest.raises(SystemExit) as e:
            bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), tools=["chat"]))
        assert "chat_a.json" in str(e.value)
        assert runner == []

    def test_an_unreachable_candidate_skips_its_chat_step(
        self, tmp_path, runner, monkeypatch
    ):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": "unreachable", "score": None, "total": None},
        )
        args = sweep_args(str(tmp_path), tools=["chat"], skip_gate=False)
        s = bench_sweep.sweep([cand("a")], args)
        assert [x["status"] for x in s["steps"]] == ["skipped-gate"]
        assert not any("bench_chat.py" in c[1] for c in runner)


class TestAgentRepeats:
    """--repeats reaches bench_agent too, so its pass^k is not a pass@1."""

    def test_the_agent_step_gets_the_sweeps_repeats(self):
        cmd = bench_sweep.tool_command(
            "agent", cand("lbl"), "/out/f.json", sweep_args("/out", repeats=3)
        )
        assert cmd[cmd.index("--repeats") + 1] == "3"
        assert "--backend" not in cmd and "--base-url" not in cmd

    def test_repeats_below_one_are_refused_before_anything_runs(self, tmp_path, runner):
        # --repeats 0 measures nothing: refuse it here, not mid-sweep.
        p = tmp_path / "cands.json"
        p.write_text(json.dumps([{"base_url": "http://h:1", "model": "m"}]))
        argv = ["--candidates", str(p), "--outdir", str(tmp_path / "out")]
        argv += ["--tools", "agent", "--skip-gate", "--repeats", "0"]
        with pytest.raises(SystemExit):
            bench_sweep.main(argv)
        assert runner == []
        assert not (tmp_path / "out").exists()


class TestGate:
    def _probe(self, monkeypatch, result):
        from orchestrant.benchmark import openai_api as bench

        monkeypatch.setattr(bench, "run_correctness_probe", lambda *a, **k: result)

    def test_a_full_score_is_ok(self, monkeypatch):
        self._probe(monkeypatch, {"score": 6, "total": 6, "wrong": 0, "truncated": 0})
        assert bench_sweep.gate(cand("a"))["verdict"] == "ok"

    def test_a_wrong_answer_is_recorded_as_wrong(self, monkeypatch):
        # A broken quantisation is FAST, so its speed numbers are meaningless.
        self._probe(monkeypatch, {"score": 4, "total": 6, "wrong": 2, "truncated": 0})
        assert bench_sweep.gate(cand("a"))["verdict"] == "wrong"

    def test_truncation_is_not_incorrectness(self, monkeypatch):
        self._probe(monkeypatch, {"score": 5, "total": 6, "wrong": 0, "truncated": 1})
        assert bench_sweep.gate(cand("a"))["verdict"] == "truncated"

    def test_an_unreachable_endpoint_is_its_own_verdict(self, monkeypatch):
        self._probe(monkeypatch, None)
        assert bench_sweep.gate(cand("a"))["verdict"] == "unreachable"

    def test_only_integrity_answers_decide_it(self, monkeypatch):
        # Capability misses only: the model's, not the lane's.
        from orchestrant.benchmark import correctness

        def block(wrong):
            return correctness.summarise(
                [
                    correctness.graded_item(
                        p, "0" if p.accepted[0] in wrong else p.accepted[0]
                    )
                    for p in correctness.CORRECTNESS_PROBES
                ]
            )

        self._probe(monkeypatch, block({"3", "5", "9.9"}))
        verdict = bench_sweep.gate(cand("a"))
        assert verdict["verdict"] == "ok"
        assert verdict["capability"]["wrong"] == 3
        assert verdict["integrity"]["wrong"] == 0
        self._probe(monkeypatch, block({"391"}))
        assert bench_sweep.gate(cand("a"))["verdict"] == "wrong"


class TestSweep:
    def _gate(self, monkeypatch, verdict):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": verdict, "score": 6, "total": 6},
        )

    def test_the_gate_runs_before_the_tools(self, tmp_path, runner, monkeypatch):
        order = []
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: (
                order.append("gate") or {"verdict": "ok", "score": 6, "total": 6}
            ),
        )
        monkeypatch.setattr(
            bench_sweep, "run_step", lambda cmd: order.append("run") or 0
        )
        bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        assert order[0] == "gate"

    def test_an_unreachable_candidate_is_not_measured(
        self, tmp_path, runner, monkeypatch
    ):
        # A dead lane yields plausible failures everywhere; only the gate tells.
        self._gate(monkeypatch, "unreachable")
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        assert [x["status"] for x in s["steps"]] == ["skipped-gate"]
        assert not any("bench_coding.py" in c[1] for c in runner)

    def test_a_degraded_candidate_is_still_measured_and_recorded(
        self, tmp_path, runner, monkeypatch
    ):
        self._gate(monkeypatch, "wrong")
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        assert s["candidates"][0]["gate"]["verdict"] == "wrong"
        assert s["steps"][0]["status"] == "ok"

    def test_the_verdict_is_written_next_to_the_results(
        self, tmp_path, runner, monkeypatch
    ):
        self._gate(monkeypatch, "ok")
        bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        summary = json.load(open(tmp_path / "_sweep.json"))
        assert summary["candidates"][0]["gate"]["verdict"] == "ok"

    def test_the_summary_is_hidden_from_the_manifest_glob(self, tmp_path, runner):
        # bench_report globs *.json; the leading underscore keeps this results-less file out.
        bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path)))
        assert os.path.basename(str(tmp_path / "_sweep.json")).startswith("_")

    def test_a_failing_tool_does_not_stop_the_sweep(self, tmp_path, monkeypatch):
        calls = []

        def fake(cmd):
            calls.append(cmd)
            return 1 if "bench_coding.py" in cmd[1] else 0

        monkeypatch.setattr(bench_sweep, "run_step", fake)
        s = bench_sweep.sweep([cand("a"), cand("b")], sweep_args(str(tmp_path)))
        assert [x["status"] for x in s["steps"]] == ["failed", "failed"]
        # ... and the manifest still ran.
        assert any(
            "bench_report.py" in " ".join(c) or "orchestrant.benchmark" in " ".join(c)
            for c in calls
        )

    def test_the_manifest_step_runs_last(self, tmp_path, runner):
        bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path)))
        assert (
            "orchestrant.benchmark" in " ".join(runner[-1])
            or "bench_report.py" in runner[-1][1]
        )
        assert "manifest" in runner[-1]

    def test_no_comparison_without_a_baseline(self, tmp_path, runner):
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path)))
        assert "compare" not in s
        assert not any("bench_compare.py" in c[1] for c in runner)

    def test_a_baseline_compares_every_written_report(self, tmp_path, monkeypatch):
        calls = []

        def fake(cmd):
            calls.append(cmd)
            if "bench_coding.py" in cmd[1]:  # the tool "wrote" its report
                open(cmd[cmd.index("--output") + 1], "w").write("{}")
            return 0

        monkeypatch.setattr(bench_sweep, "run_step", fake)
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), baseline="prev"))
        assert len(s["compare"]) == 1
        assert s["compare"][0]["regressed"] is False

    def test_a_report_the_tool_never_wrote_is_not_compared(self, tmp_path, runner):
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), baseline="prev"))
        assert s["compare"] == []

    def test_a_regression_is_recorded_not_hidden(self, tmp_path, monkeypatch):
        def fake(cmd):
            if "bench_coding.py" in cmd[1]:
                open(cmd[cmd.index("--output") + 1], "w").write("{}")
                return 0
            return 1 if "bench_compare.py" in cmd[1] else 0

        monkeypatch.setattr(bench_sweep, "run_step", fake)
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), baseline="prev"))
        assert s["compare"][0]["regressed"] is True
        assert s["compare"][0]["conditions_differ"] is False

    def test_a_withheld_verdict_is_recorded_as_conditions_differ(
        self, tmp_path, monkeypatch
    ):
        # Exit 4 is not a regression, and not a pass either.
        def fake(cmd):
            if "bench_coding.py" in cmd[1]:
                open(cmd[cmd.index("--output") + 1], "w").write("{}")
                return 0
            return 4 if "bench_compare.py" in cmd[1] else 0

        monkeypatch.setattr(bench_sweep, "run_step", fake)
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), baseline="prev"))
        record = s["compare"][0]
        assert record["conditions_differ"] is True and record["regressed"] is False


class TestMainValidation:
    def _candidates(self, tmp_path):
        p = tmp_path / "cands.json"
        p.write_text(
            json.dumps([{"base_url": "http://h:1", "model": "org/M", "label": "a"}])
        )
        return str(p)

    def test_an_unknown_tool_name_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            bench_sweep.main(
                [
                    "--candidates",
                    self._candidates(tmp_path),
                    "--outdir",
                    str(tmp_path / "out"),
                    "--tools",
                    "coding,telepathy",
                ]
            )

    def test_an_empty_tool_list_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            bench_sweep.main(
                [
                    "--candidates",
                    self._candidates(tmp_path),
                    "--outdir",
                    str(tmp_path / "out"),
                    "--tools",
                    ",",
                ]
            )

    def test_a_missing_baseline_is_caught_before_any_measurement(
        self, tmp_path, runner
    ):
        # Otherwise the sweep measures for hours and then fails on the compare.
        with pytest.raises(SystemExit) as e:
            bench_sweep.main(
                [
                    "--candidates",
                    self._candidates(tmp_path),
                    "--outdir",
                    str(tmp_path / "out"),
                    "--tools",
                    "coding",
                    "--skip-gate",
                    "--baseline",
                    "no-such-baseline",
                ]
            )
        assert "no-such-baseline" in str(e.value)
        assert runner == []

    def test_an_empty_candidates_file_is_refused(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("[]")
        with pytest.raises(SystemExit):
            bench_sweep.main(
                [
                    "--candidates",
                    str(p),
                    "--outdir",
                    str(tmp_path / "o"),
                    "--tools",
                    "coding",
                    "--skip-gate",
                ]
            )

    def test_a_whole_run_writes_the_summary_and_exits_zero(self, tmp_path, runner):
        rc = bench_sweep.main(
            [
                "--candidates",
                self._candidates(tmp_path),
                "--outdir",
                str(tmp_path / "out"),
                "--tools",
                "coding",
                "--skip-gate",
            ]
        )
        assert rc == 0
        assert os.path.exists(tmp_path / "out" / "_sweep.json")

    def test_a_failed_tool_makes_the_sweep_exit_non_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_sweep, "run_step", lambda cmd: 3)
        rc = bench_sweep.main(
            [
                "--candidates",
                self._candidates(tmp_path),
                "--outdir",
                str(tmp_path / "out"),
                "--tools",
                "coding",
                "--skip-gate",
            ]
        )
        assert rc == 1


class TestASweepThatMeasuredNothingFailsLoudly:
    """A sweep whose every candidate gated `unreachable` fails instead of exiting 0."""

    def _gate(self, monkeypatch, verdict):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": verdict, "score": 0, "total": 6},
        )

    def test_an_all_skipped_sweep_exits_non_zero(self, tmp_path, runner, monkeypatch):
        self._gate(monkeypatch, "unreachable")
        monkeypatch.setattr(bench_cli, "load_candidates", lambda *a, **k: [cand("a")])
        with pytest.raises(SystemExit) as e:
            bench_sweep.main(
                [
                    "--candidates",
                    "c.json",
                    "--outdir",
                    str(tmp_path),
                    "--tools",
                    "coding",
                ]
            )
        assert "nothing was measured" in str(e.value)

    def test_a_sweep_that_measured_something_still_exits_zero(
        self, tmp_path, runner, monkeypatch
    ):
        self._gate(monkeypatch, "ok")
        monkeypatch.setattr(bench_cli, "load_candidates", lambda *a, **k: [cand("a")])
        assert (
            bench_sweep.main(
                [
                    "--candidates",
                    "c.json",
                    "--outdir",
                    str(tmp_path),
                    "--tools",
                    "coding",
                ]
            )
            == 0
        )

    def test_a_failed_step_keeps_its_own_exit_code_and_wording(
        self, tmp_path, runner, monkeypatch
    ):
        # `not any(status == ok)` would have replaced this with the gate message.
        self._gate(monkeypatch, "ok")
        monkeypatch.setattr(bench_sweep, "run_step", lambda cmd: 1)
        monkeypatch.setattr(bench_cli, "load_candidates", lambda *a, **k: [cand("a")])
        assert (
            bench_sweep.main(
                [
                    "--candidates",
                    "c.json",
                    "--outdir",
                    str(tmp_path),
                    "--tools",
                    "coding",
                ]
            )
            == 1
        )


class TestTheSweepSummaryHoldsTheArgv:
    """`_sweep.json` holds every step's exact argv."""

    def test_every_step_records_its_command(self, tmp_path, runner, monkeypatch):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": "ok", "score": 6, "total": 6},
        )
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        assert s["steps"] and all(step["argv"] for step in s["steps"])
        assert "bench_coding.py" in " ".join(s["steps"][0]["argv"])
        assert s["manifest"]["argv"]

    def test_a_skipped_step_records_the_command_it_would_have_run(
        self, tmp_path, runner, monkeypatch
    ):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": "unreachable", "score": None, "total": None},
        )
        s = bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        assert "bench_coding.py" in " ".join(s["steps"][0]["argv"])

    def test_the_argv_survives_into_the_file(self, tmp_path, runner, monkeypatch):
        monkeypatch.setattr(
            bench_sweep,
            "gate",
            lambda c, **k: {"verdict": "ok", "score": 6, "total": 6},
        )
        bench_sweep.sweep([cand("a")], sweep_args(str(tmp_path), skip_gate=False))
        with open(tmp_path / "_sweep.json") as f:
            written = json.load(f)
        assert "bench_coding.py" in " ".join(written["steps"][0]["argv"])
