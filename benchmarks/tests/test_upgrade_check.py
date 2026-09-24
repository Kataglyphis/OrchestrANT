"""Tests for the upgrade check: the post-upgrade protocol as one command.

Nothing here runs a benchmark or reaches a lane. `run_logged` (the subprocess
seam), `lane_answers` and `runtime_info` (the network seams) are replaced in
every test that executes a plan; the stand-in writes the report each tool
would and returns the exit code the test chose. The two tests of `run_logged`
itself run a one-line Python child, never a tool.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import upgrade_check as uc  # noqa: E402

from orchestrant.benchmark import openai_api  # noqa: E402

REGISTRY = {
    "geniex-npu": {"base_url": "http://127.0.0.1:18181", "model": "qualcomm/Q:W4A16"},
    "geniex-cpu": {"base_url": "http://127.0.0.1:18184", "model": "unsloth/Q:Q4_0"},
}
RUNTIME = {
    "server": "geniex",
    "cli": "v0.8.0",
    "qairt": "2.46",
    "llama_cpp": "abc1234",
    "serve_args": ["serve", "--compute", "npu", "--log", "none"],
    "started": 1790237497.0,
    "verified": True,
    "source": "lane process pid 13908",
}
CONTEXT = {
    "host": "lab",
    "os": "Windows 11",
    "machine": "ARM64",
    "python": "3.14.6",
    "git_sha": "1dcb81c",
    "dirty": False,
}


def contract_report(answer="yes"):
    checks = [{"id": "max_tokens_honoured", "answer": answer}]
    return {
        "benchmark": "bench_contract",
        "reports": [{"label": "x", "checks": checks}],
    }


def speed_report(correctness=None):
    # The speed runner's shape (v070r2-npu-speed-answer.json): `correctness`
    # is null unless --correctness ran.
    config = {"prompts_requested": 9, "prompts_completed": 9}
    return {"model": "m", "results": [], "config": config, "correctness": correctness}


GATE = {"score": 6, "total": 6, "wrong": 0, "truncated": 0, "errors": 0}
REPORTS = {
    "contract": contract_report(),
    "speed": speed_report(GATE),
    "speed-answer": speed_report(),
    "tools": {"benchmark": "bench_tools", "reports": [{"label": "x", "total": 27}]},
    "coding": {"benchmark": "bench_coding", "reports": [{"label": "x", "total": 31}]},
}
ORDER = ("contract", "contract-diff", "speed", "speed-answer", "tools", "coding")
# bench_compare --dir's last line, printed only when it finished.
PAIRED = "\n  5 report(s) paired, 0 with nothing to compare, 0 gone, 0 new\n"


def diff_line(check, before, after):
    """`contract --diff`'s own row for an answer that moved."""
    return f"  CHANGED  {check:<28} {before:<8} -> {after}\n"


class Runner:
    """Stands in for run_logged: writes what the tool would, returns `rc[kind]`."""

    def __init__(self):
        """Every step exits 0 and writes its report until a test says otherwise."""
        self.calls = []
        self.rc = {}
        self.logs = {}
        self.reports = {k: json.loads(json.dumps(v)) for k, v in REPORTS.items()}
        self.interrupt = None
        self.manifests = []

    @staticmethod
    def kind(log_path):
        name = os.path.basename(log_path)
        return "compare" if name == "compare.log" else name.split("-", 2)[2][:-4]

    def __call__(self, argv, log_path, cwd, env):
        kind = self.kind(log_path)
        out = os.path.dirname(log_path)
        manifest = os.path.join(out, "MANIFEST.md")
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8") as f:
                self.manifests.append(f.read())
        self.calls.append((kind, argv))
        if kind == self.interrupt:
            raise KeyboardInterrupt
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(self.logs.get(kind, "ok\n"))
        report = self.reports.get(kind)
        if report is not None:
            with open(log_path[: -len(".log")] + ".json", "w") as f:
                json.dump(report, f)
        return self.rc.get(kind, 0)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(openai_api, "load_backends", lambda path=None: (REGISTRY, None))
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    # A registry named by the environment is forwarded into WSL; not here.
    monkeypatch.delenv("LLM_BACKENDS", raising=False)
    monkeypatch.setattr(uc, "needs_wsl", lambda: False)


@pytest.fixture
def lab(monkeypatch):
    runner = Runner()
    monkeypatch.setattr(uc, "run_logged", runner)
    monkeypatch.setattr(uc, "lane_answers", lambda url: True)
    monkeypatch.setattr(uc, "runtime_info", lambda url: dict(RUNTIME))
    monkeypatch.setattr(uc, "check_context", lambda: dict(CONTEXT))
    return runner


def parse(out, *extra, lanes="geniex-npu,geniex-cpu"):
    args = uc.parse_args(["--lanes", lanes, "--out", str(out), *extra])
    args.overflow = uc.parse_overflow(args.overflow_tokens, lanes.split(","))
    return args


def plan_of(out, *extra, lanes="geniex-npu,geniex-cpu"):
    return uc.plan(uc.resolve_lanes(lanes), parse(out, *extra, lanes=lanes))


def by_kind(steps, lane="geniex-npu"):
    return {s["kind"]: s for s in steps if s["lane"] in (lane, None)}


def previous_run(tmp_path, reports=None):
    prev = tmp_path / "prev"
    prev.mkdir()
    for kind, report in (reports or REPORTS).items():
        for lane in REGISTRY:
            (prev / f"{lane}-{kind}.json").write_text(json.dumps(report))
    return prev


def records(out):
    with open(out / "steps.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return [r for r in rows if r["type"] == "step"]


def manifest(out):
    return (out / "MANIFEST.md").read_text(encoding="utf-8")


class TestPlan:
    def test_each_lane_runs_its_steps_in_the_protocol_order_then_compare(
        self, tmp_path
    ):
        # The order is part of the result: the contract's power_mode check
        # reloads the model, and a lane is never measured beside another.
        steps = plan_of(tmp_path / "run", "--previous", str(previous_run(tmp_path)))
        expected = [(lane, kind) for lane in REGISTRY for kind in ORDER]
        assert [(s["lane"], s["kind"]) for s in steps] == [*expected, (None, "compare")]

    def test_steps_given_out_of_order_still_run_in_order(self, tmp_path):
        steps = plan_of(tmp_path / "run", "--steps", "coding,speed,contract")
        kinds = [s["kind"] for s in steps if s["lane"] == "geniex-npu"]
        assert kinds == ["contract", "speed", "coding"]

    def test_the_commands_are_the_protocols(self, tmp_path):
        out = tmp_path / "run"
        steps = by_kind(plan_of(out, "--previous", str(previous_run(tmp_path))))
        contract = steps["contract"]["argv"]
        assert contract[1:5] == ["-m", "orchestrant.benchmark", "contract", "--backend"]
        assert contract[-1] == str(out / "geniex-npu-contract.json")
        speed = steps["speed"]["argv"]
        assert "--stream" in speed and "--correctness" in speed
        answer = steps["speed-answer"]["argv"]
        assert answer[answer.index("--max-tokens") + 1] == "2048"
        assert "--correctness" not in answer
        tools = steps["tools"]["argv"]
        assert tools[1].endswith("bench_tools.py")
        assert tools[tools.index("--repeats") + 1] == "3"
        assert tools[tools.index("--label") + 1] == "geniex-npu"
        coding = steps["coding"]["argv"]
        assert "--keep-output" in coding
        assert coding[coding.index("--task-set") + 1] == "all"
        compare = steps["compare"]["argv"]
        assert compare[1].endswith("bench_compare.py")
        assert compare[2:] == ["--dir", str(tmp_path / "prev"), str(out)]

    def test_every_script_it_names_exists(self, tmp_path):
        for step in plan_of(
            tmp_path / "run", "--previous", str(previous_run(tmp_path))
        ):
            if step["argv"][1] != "-m":
                assert os.path.exists(step["argv"][1]), step["kind"]

    def test_files_are_named_after_lane_and_step(self, tmp_path):
        steps = plan_of(tmp_path / "run", "--previous", str(previous_run(tmp_path)))
        names = {s["output"] or s["log"] for s in steps}
        assert "geniex-cpu-speed-answer.json" in names
        assert "geniex-npu-contract-diff.log" in names
        assert "compare.log" in names

    def test_no_correctness_drops_only_the_gate(self, tmp_path):
        speed = by_kind(plan_of(tmp_path / "run", "--no-correctness"))["speed"]
        assert "--correctness" not in speed["argv"]
        assert "--stream" in speed["argv"]

    def test_overflow_tokens_reach_only_their_lane(self, tmp_path):
        # 6000 tokens overflow the NPU bundle; on a 16k GGUF lane they would be
        # a prompt that fits and prefills for a minute.
        steps = plan_of(tmp_path / "run", "--overflow-tokens", "geniex-npu=6000")
        npu = by_kind(steps)["contract"]["argv"]
        cpu = by_kind(steps, "geniex-cpu")["contract"]["argv"]
        assert npu[npu.index("--overflow-tokens") + 1] == "6000"
        assert "--overflow-tokens" not in cpu

    def test_a_lane_without_a_previous_report_is_not_diffed(self, tmp_path):
        prev = previous_run(tmp_path, {"speed": REPORTS["speed"]})
        diff = by_kind(plan_of(tmp_path / "run", "--previous", str(prev)))
        assert "has no geniex-npu-contract.json" in diff["contract-diff"]["skip"]

    def test_without_previous_the_comparison_is_skipped_with_a_reason(self, tmp_path):
        compare = by_kind(plan_of(tmp_path / "run"))["compare"]
        assert compare["argv"] is None
        assert "--previous" in compare["skip"]


class TestRefusals:
    def _refused(self, argv, lab=None):
        with pytest.raises(SystemExit) as e:
            uc.main(argv)
        if lab is not None:
            assert lab.calls == []
        return str(e.value)

    def test_an_existing_out_directory_is_refused_even_when_empty(self, tmp_path, lab):
        out = tmp_path / "run"
        out.mkdir()
        msg = self._refused(["--lanes", "geniex-npu", "--out", str(out)], lab)
        assert "already exists" in msg
        assert os.listdir(out) == []

    def test_a_previous_that_is_not_a_directory_is_refused(self, tmp_path, lab):
        argv = ["--lanes", "geniex-npu", "--out", str(tmp_path / "run")]
        msg = self._refused([*argv, "--previous", str(tmp_path / "gone")], lab)
        assert "not a directory" in msg
        assert not (tmp_path / "run").exists()

    def test_an_environment_url_override_is_refused(self, tmp_path, lab, monkeypatch):
        # LLM_BASE_URL beats --backend in every tool, so both lanes would
        # have measured the one endpoint it names.
        monkeypatch.setenv("LLM_BASE_URL", "http://elsewhere:1")
        argv = ["--lanes", "geniex-npu,geniex-cpu", "--out", str(tmp_path / "run")]
        assert "LLM_BASE_URL" in self._refused(argv, lab)

    def test_an_unknown_lane_is_refused_naming_the_known_ones(self, tmp_path):
        argv = ["--lanes", "geniex-npu,geniex-gpu", "--out", str(tmp_path / "run")]
        msg = self._refused(argv)
        assert "geniex-gpu" in msg and "geniex-cpu" in msg

    def test_a_lane_named_twice_is_refused(self, tmp_path):
        argv = ["--lanes", "geniex-npu,geniex-npu", "--out", str(tmp_path / "r")]
        assert "more than once" in self._refused(argv)

    def test_an_unknown_step_is_refused(self, tmp_path):
        argv = ["--lanes", "geniex-npu", "--out", str(tmp_path / "r")]
        assert "choose from" in self._refused([*argv, "--steps", "speed,vibes"])

    def test_overflow_for_a_lane_outside_the_check_is_refused(self, tmp_path):
        argv = ["--lanes", "geniex-npu", "--out", str(tmp_path / "r")]
        msg = self._refused([*argv, "--overflow-tokens", "geniex-cpu=6000"])
        assert "LANE=N" in msg

    def test_wsl_is_refused_where_coding_runs_natively(self, tmp_path):
        argv = ["--lanes", "geniex-npu", "--out", str(tmp_path / "r"), "--wsl"]
        assert "natively" in self._refused(argv)

    def test_a_dry_run_creates_and_contacts_nothing(self, tmp_path, lab, capsys):
        out = tmp_path / "run"
        assert uc.main(["--lanes", "geniex-npu", "--out", str(out), "--dry-run"]) == 0
        assert not out.exists()
        assert lab.calls == []
        assert "geniex-npu-speed-answer.json" in capsys.readouterr().out


class TestCodingOnWindows:
    def _args(self, tmp_path, *extra):
        return parse(tmp_path / "run", *extra, lanes="geniex-npu")

    def test_without_wsl_coding_is_skipped_and_its_command_kept(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        monkeypatch.setattr(uc, "HERE", r"C:\GitHub\OrchestrANT\benchmarks")
        monkeypatch.setattr(uc, "REPO_ROOT", r"C:\GitHub\OrchestrANT")
        args = self._args(tmp_path)
        args.out = r"C:\GitHub\OrchestrANT\benchmarks\benchmark_results\run"
        step = uc.coding_step("geniex-npu", "geniex-npu", args)
        assert "--wsl" in step["skip"]
        assert step["argv"][:2] == ["wsl", "-d"]

    def test_with_wsl_it_runs_in_the_named_distro_on_mnt_paths(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        monkeypatch.setattr(uc, "HERE", r"C:\GitHub\OrchestrANT\benchmarks")
        monkeypatch.setattr(uc, "REPO_ROOT", r"C:\GitHub\OrchestrANT")
        args = self._args(tmp_path, "--wsl-distro", "Ubuntu-26.04")
        args.wsl = True
        args.out = r"C:\GitHub\OrchestrANT\benchmarks\benchmark_results\run"
        step = uc.coding_step("geniex-npu", "geniex-npu", args)
        assert step["skip"] is None
        assert step["argv"][:6] == ["wsl", "-d", "Ubuntu-26.04", "--", "bash", "-lc"]
        script = step["argv"][6]
        assert script.startswith("cd /mnt/c/GitHub/OrchestrANT/benchmarks && ")
        assert "PYTHONPATH=/mnt/c/GitHub/OrchestrANT " in script
        # `~` must reach bash unquoted, or it never expands.
        assert " ~/.local/bin/uv run --no-project" in script
        assert script.endswith(
            "--output /mnt/c/GitHub/OrchestrANT/benchmarks/benchmark_results/run/"
            "geniex-npu-coding.json"
        )

    def test_the_registry_named_on_windows_is_the_one_read_in_wsl(
        self, tmp_path, monkeypatch
    ):
        # Windows' environment does not cross into WSL: without this the child
        # resolves --backend from the repository's registry, not the lanes'.
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        monkeypatch.setattr(uc, "HERE", r"C:\GitHub\OrchestrANT\benchmarks")
        monkeypatch.setattr(uc, "REPO_ROOT", r"C:\GitHub\OrchestrANT")
        monkeypatch.setenv("LLM_BACKENDS", r"D:\lab\backends.json")
        args = self._args(tmp_path)
        args.wsl = True
        args.out = r"C:\GitHub\OrchestrANT\benchmarks\benchmark_results\run"
        script = uc.coding_step("geniex-npu", "geniex-npu", args)["argv"][6]
        assert " LLM_BACKENDS=/mnt/d/lab/backends.json PYTHONPATH=" in script

    def test_a_relative_registry_is_made_absolute_for_the_children(
        self, tmp_path, lab, monkeypatch
    ):
        # They run in benchmarks/ (or WSL), not where the check was started.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_BACKENDS", "backends.json")
        argv = ["--lanes", "geniex-npu", "--out", str(tmp_path / "r"), "--dry-run"]
        uc.main(argv)
        assert os.environ["LLM_BACKENDS"] == str(tmp_path / "backends.json")

    def test_a_path_wsl_cannot_reach_is_refused_with_wsl(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        args = self._args(tmp_path)
        args.wsl = True
        args.out = r"\\server\share\run"
        with pytest.raises(SystemExit, match="--wsl"):
            uc.coding_step("geniex-npu", "geniex-npu", args)

    @pytest.mark.parametrize(
        "path, expected",
        [
            (r"C:\GitHub\OrchestrANT", "/mnt/c/GitHub/OrchestrANT"),
            ("D:/data/runs/", "/mnt/d/data/runs"),
            ("c:\\", "/mnt/c"),
        ],
    )
    def test_to_wsl_path(self, path, expected):
        assert uc.to_wsl_path(path) == expected

    def test_a_skipped_coding_step_does_not_fail_the_check(
        self, tmp_path, lab, monkeypatch
    ):
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        monkeypatch.setattr(uc, "wsl_argv", lambda args, script, tool_args: ["wsl"])
        out = tmp_path / "run"
        assert uc.main(["--lanes", "geniex-npu", "--out", str(out)]) == 0
        coding = [r for r in records(out) if r["step"] == "coding"][0]
        assert coding["status"] == "skipped" and coding["rc"] is None
        assert "coding" not in [kind for kind, _ in lab.calls]


class TestRun:
    def _run(self, tmp_path, *extra, lanes="geniex-npu,geniex-cpu"):
        out = tmp_path / "run"
        return uc.main(["--lanes", lanes, "--out", str(out), *extra]), out

    def test_a_clean_run_exits_0_and_logs_every_step(self, tmp_path, lab):
        code, out = self._run(tmp_path)
        assert code == 0
        rows = records(out)
        assert [(r["lane"], r["step"]) for r in rows][:5] == [
            ("geniex-npu", kind) for kind in ORDER if kind != "contract-diff"
        ]
        for row in rows[:-1]:
            assert row["status"] == "ok" and row["rc"] == 0
            assert row["argv"] and row["start"] and row["end"]
            assert row["duration_s"] is not None
        assert rows[-1]["step"] == "compare" and rows[-1]["status"] == "skipped"

    def test_only_reports_are_json_in_the_run_directory(self, tmp_path, lab):
        # bench_compare --dir reads every *.json there; anything else is fatal
        # to the NEXT upgrade check's comparison.
        _, out = self._run(tmp_path)
        written = {f for f in os.listdir(out) if f.endswith(".json")}
        expected = {f"{lane}-{kind}.json" for lane in REGISTRY for kind in REPORTS}
        assert written == expected

    def test_the_manifest_maps_each_file_to_its_command_and_exit(self, tmp_path, lab):
        _, out = self._run(tmp_path)
        text = manifest(out)
        assert "**Verdict: OK** (exit 0)" in text
        speed = [r for r in records(out) if r["step"] == "speed"][0]
        row = [line for line in text.splitlines() if "`geniex-npu-speed.json`" in line]
        assert len(row) == 1
        assert uc.format_command(speed["argv"]) in row[0]
        assert "| 0 | ok |" in row[0]
        assert "geniex v0.8.0 (QAIRT 2.46, llama.cpp abc1234) (verified)" in text

    def test_a_failed_step_fails_the_check_and_the_rest_still_run(self, tmp_path, lab):
        lab.rc["tools"] = 1
        code, out = self._run(tmp_path)
        assert code == 1
        assert [k for k, _ in lab.calls].count("coding") == 2
        tools = [r for r in records(out) if r["step"] == "tools"]
        assert [r["status"] for r in tools] == ["failed", "failed"]
        assert "**Verdict: FAILED** (exit 1)" in manifest(out)

    def test_exit_0_without_the_report_is_a_failure(self, tmp_path, lab):
        del lab.reports["speed"]
        code, out = self._run(tmp_path, lanes="geniex-npu")
        speed = [r for r in records(out) if r["step"] == "speed"][0]
        assert code == 1
        assert speed["status"] == "failed" and "wrote no" in speed["reason"]

    def test_a_contract_that_got_no_answer_is_a_failure(self, tmp_path, lab):
        # The contract exits 0 with every check `error` when the lane dies.
        lab.reports["contract"] = contract_report("error")
        code, out = self._run(tmp_path, lanes="geniex-npu")
        assert code == 1
        assert records(out)[0]["status"] == "failed"

    def test_a_speed_run_with_no_completed_prompt_is_a_failure(self, tmp_path, lab):
        lab.reports["speed-answer"]["config"]["prompts_completed"] = 0
        code, _ = self._run(tmp_path, lanes="geniex-npu")
        assert code == 1

    def test_a_speed_run_that_lost_a_prompt_is_a_failure(self, tmp_path, lab):
        # The runner exits 0 with errored prompts left out of its table.
        lab.reports["speed-answer"]["config"]["prompts_completed"] = 8
        code, out = self._run(tmp_path, lanes="geniex-npu")
        answer = [r for r in records(out) if r["step"] == "speed-answer"][0]
        assert code == 1
        assert answer["status"] == "failed"
        assert answer["reason"] == "8 of 9 prompts completed"

    def test_a_wrong_answer_from_the_gate_fails_the_speed_step(self, tmp_path, lab):
        # The runner exits 0 whatever the gate scored; a fast wrong answer is
        # the broken kernel the gate is there to catch.
        lab.reports["speed"]["correctness"] = {**GATE, "score": 4, "wrong": 2}
        code, out = self._run(tmp_path, lanes="geniex-npu")
        speed = [r for r in records(out) if r["step"] == "speed"][0]
        assert code == 1
        assert speed["status"] == "failed" and "2 of 6 answers wrong" in speed["reason"]

    def test_a_gate_that_scored_nothing_fails_the_speed_step(self, tmp_path, lab):
        lab.reports["speed"]["correctness"] = None  # every probe errored
        code, out = self._run(tmp_path, lanes="geniex-npu")
        speed = [r for r in records(out) if r["step"] == "speed"][0]
        assert code == 1 and "gate scored no probe" in speed["reason"]

    def test_without_the_gate_no_correctness_block_is_expected(self, tmp_path, lab):
        lab.reports["speed"]["correctness"] = None
        code, _ = self._run(tmp_path, "--no-correctness", lanes="geniex-npu")
        assert code == 0

    def test_a_report_of_a_foreign_shape_fails_its_step_not_the_check(
        self, tmp_path, lab
    ):
        # Valid JSON, not an object: this used to escape as a TypeError from
        # the manifest, with the step's status null and the other lane unrun.
        lab.reports["contract"] = [1, 2, 3]
        code, out = self._run(tmp_path)
        contract = [r for r in records(out) if r["step"] == "contract"]
        assert code == 1
        assert [r["status"] for r in contract] == ["failed", "failed"]
        assert "unreadable report" in contract[0]["reason"]
        assert [k for k, _ in lab.calls].count("coding") == 2
        assert "**Verdict: FAILED** (exit 1)" in manifest(out)

    def test_a_check_that_ran_nothing_does_not_pass(self, tmp_path, lab, monkeypatch):
        # Coding only, on Windows without --wsl: every step skipped.
        monkeypatch.setattr(uc, "needs_wsl", lambda: True)
        monkeypatch.setattr(uc, "wsl_argv", lambda args, script, tool_args: ["wsl"])
        code, out = self._run(tmp_path, "--steps", "coding", lanes="geniex-npu")
        assert code == 1
        assert lab.calls == []
        assert "NOTHING RAN" in manifest(out).splitlines()[2]

    def test_an_unreachable_lane_is_not_measured(self, tmp_path, lab, monkeypatch):
        monkeypatch.setattr(uc, "lane_answers", lambda url: not url.endswith("18184"))
        code, out = self._run(tmp_path)
        assert code == 1
        assert all(
            argv[argv.index("--backend") + 1] == "geniex-npu" for _, argv in lab.calls
        )
        cpu = [r for r in records(out) if r["lane"] == "geniex-cpu"]
        assert {r["status"] for r in cpu} == {"skipped"}
        assert "did not answer" in cpu[0]["reason"]

    def test_a_lane_relaunched_mid_check_fails_it(self, tmp_path, lab, monkeypatch):
        starts = iter([1.0, 2.0])
        monkeypatch.setattr(
            uc, "runtime_info", lambda url: {**RUNTIME, "started": next(starts)}
        )
        code, out = self._run(tmp_path, lanes="geniex-npu")
        assert code == 1
        assert "relaunched" in manifest(out)

    def test_an_upgrade_mid_check_is_named(self, tmp_path, lab, monkeypatch):
        builds = iter(["v0.8.0", "v0.8.1"])
        monkeypatch.setattr(
            uc, "runtime_info", lambda url: {**RUNTIME, "cli": next(builds)}
        )
        code, out = self._run(tmp_path, lanes="geniex-npu")
        assert code == 1
        assert (
            "geniex v0.8.0 (QAIRT 2.46, llama.cpp abc1234) -> geniex v0.8.1"
            in manifest(out)
        )

    def test_the_manifest_is_rewritten_after_every_step(self, tmp_path, lab):
        self._run(tmp_path, lanes="geniex-npu")
        assert len(lab.manifests) == 5
        second = lab.manifests[1]
        assert "INCOMPLETE" in second and "| pending |" in second
        assert "`geniex-npu-contract.json`" in second

    def test_ctrl_c_keeps_the_record_and_exits_130(self, tmp_path, lab):
        lab.interrupt = "speed"
        code, out = self._run(tmp_path, lanes="geniex-npu")
        assert code == 130
        rows = records(out)
        assert [r["status"] for r in rows] == ["ok", "interrupted"]
        text = manifest(out)
        assert "INTERRUPTED" in text and "not run: interrupted" in text


class TestComparison:
    def _run(self, tmp_path, prev, lanes="geniex-npu"):
        out = tmp_path / "run"
        argv = ["--lanes", lanes, "--out", str(out), "--previous", str(prev)]
        return uc.main(argv), out

    def _compare(self, out):
        return [r for r in records(out) if r["step"] == "compare"][0]

    def test_no_regression_exits_0(self, tmp_path, lab):
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 0
        assert self._compare(out)["status"] == "ok"

    def test_a_regression_is_its_own_status_and_fails_the_check(self, tmp_path, lab):
        lab.rc["compare"] = 1
        lab.logs["compare"] = f"  geniex-npu-tools.json\n    REGRESSION\n{PAIRED}"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 1
        assert self._compare(out)["status"] == "regression"
        assert "REGRESSION" in manifest(out).splitlines()[2]

    def test_a_regression_then_a_crash_is_a_failure(self, tmp_path, lab):
        # One pair said REGRESSION, then an unreadable report stopped it
        # before its summary: the comparison did not finish.
        lab.rc["compare"] = 1
        lab.logs["compare"] = "  a.json\n    REGRESSION\n\n  b.json: not a report\n"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 1
        assert self._compare(out)["status"] == "failed"

    def test_exit_1_without_a_verdict_is_a_failure_not_a_regression(
        self, tmp_path, lab
    ):
        # bench_compare also exits 1 on an unreadable report.
        lab.rc["compare"] = 1
        lab.logs["compare"] = "x.json: not a benchmark report\n"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 1
        assert self._compare(out)["status"] == "failed"

    def test_exit_3_is_nothing_compared_and_not_a_pass(self, tmp_path, lab):
        lab.rc["compare"] = 3
        code, out = self._run(tmp_path, previous_run(tmp_path))
        compare = self._compare(out)
        assert code == 1
        assert (compare["rc"], compare["status"]) == (3, "nothing-compared")
        assert "NOTHING COMPARED" in manifest(out)

    def test_exit_4_is_conditions_differ_and_fails_the_check(self, tmp_path, lab):
        # A speed or timing verdict was withheld for load: neither a pass nor a
        # regression, and the remedy is a re-run on a quiet host.
        lab.rc["compare"] = 4
        withheld = "    WITHHELD for load: x per-attempt time\n"
        lab.logs["compare"] = f"  a.json\n    CONDITIONS DIFFER\n{withheld}{PAIRED}"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        compare = self._compare(out)
        assert code == 1
        assert (compare["rc"], compare["status"]) == (4, "conditions-differ")
        # The busy side may be the previous run: re-running this one alone
        # on a quiet host would be refused again.
        assert "re-run the busy side on a quiet host" in compare["reason"]
        text = manifest(out)
        assert "CONDITIONS DIFFER" in text.splitlines()[2]
        assert "(all lanes, compare): conditions-differ -- " in text
        assert "4 = CONDITIONS DIFFER" in text

    def test_exit_4_that_did_not_finish_is_a_failure(self, tmp_path, lab):
        lab.rc["compare"] = 4
        lab.logs["compare"] = "  a.json\n    CONDITIONS DIFFER\n"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 1 and self._compare(out)["status"] == "failed"

    def test_a_regression_beside_a_withheld_verdict_is_a_regression(
        self, tmp_path, lab
    ):
        lab.rc["compare"] = 1
        withheld = "    WITHHELD for load: x per-attempt time\n"
        lab.logs["compare"] = f"  a.json\n    REGRESSION\n{withheld}{PAIRED}"
        code, out = self._run(tmp_path, previous_run(tmp_path))
        assert code == 1 and self._compare(out)["status"] == "regression"

    def test_no_shared_file_name_is_nothing_compared_without_running_it(
        self, tmp_path, lab
    ):
        # bench_compare --dir exits 1 on that -- its code for a regression.
        prev = tmp_path / "prev"
        prev.mkdir()
        (prev / "v070-npu-speed.json").write_text(json.dumps(REPORTS["speed"]))
        code, out = self._run(tmp_path, prev)
        assert code == 1
        assert "compare" not in [kind for kind, _ in lab.calls]
        assert self._compare(out)["status"] == "nothing-compared"

    def test_a_moved_contract_answer_is_listed_not_failed(self, tmp_path, lab):
        prev = previous_run(tmp_path, {**REPORTS, "contract": contract_report("no")})
        lab.rc["contract-diff"] = 1
        lab.logs["contract-diff"] = diff_line("max_tokens_honoured", "no", "yes")
        code, out = self._run(tmp_path, prev)
        diff = [r for r in records(out) if r["step"] == "contract-diff"][0]
        assert code == 0
        assert diff["status"] == "changed"
        assert diff["changes"] == [["max_tokens_honoured", "no", "yes"]]
        assert "- geniex-npu: `max_tokens_honoured` no -> yes" in manifest(out)

    def test_a_diff_that_exits_1_with_nothing_moved_is_a_failure(self, tmp_path, lab):
        lab.rc["contract-diff"] = 1
        code, out = self._run(tmp_path, previous_run(tmp_path))
        diff = [r for r in records(out) if r["step"] == "contract-diff"][0]
        assert code == 1 and diff["status"] == "failed"

    def test_a_diff_that_crashed_is_a_failure_even_when_answers_moved(
        self, tmp_path, lab
    ):
        # Exit 1 is also a traceback: "changed" needs the tool's CHANGED rows.
        prev = previous_run(tmp_path, {**REPORTS, "contract": contract_report("no")})
        lab.rc["contract-diff"] = 1
        lab.logs["contract-diff"] = "Traceback (most recent call last):\n"
        code, out = self._run(tmp_path, prev)
        diff = [r for r in records(out) if r["step"] == "contract-diff"][0]
        assert code == 1 and diff["status"] == "failed"

    def test_no_diff_after_a_contract_that_got_no_answer(self, tmp_path, lab):
        # Every check `error` would read as every answer moving: that is the
        # lane dying, which the contract step already failed on.
        lab.reports["contract"] = contract_report("error")
        code, out = self._run(tmp_path, previous_run(tmp_path))
        diff = [r for r in records(out) if r["step"] == "contract-diff"][0]
        assert code == 1
        assert diff["status"] == "skipped" and "a step that failed" in diff["reason"]
        assert "contract-diff" not in [kind for kind, _ in lab.calls]
        assert "## Contract answers that moved" not in manifest(out)

    def test_the_previous_runtime_is_named_beside_the_new_one(self, tmp_path, lab):
        old = {
            **contract_report(),
            "provenance": {"runtime": {**RUNTIME, "cli": "v0.7.0"}},
        }
        code, out = self._run(
            tmp_path, previous_run(tmp_path, {**REPORTS, "contract": old})
        )
        assert code == 0
        assert "| geniex v0.7.0 (QAIRT 2.46, llama.cpp abc1234) |" in manifest(out)


class TestRunLogged:
    def test_output_reaches_the_log_and_the_exit_code_is_returned(
        self, tmp_path, capsys
    ):
        log = tmp_path / "step.log"
        argv = [
            sys.executable,
            "-c",
            "print('hello from the step'); raise SystemExit(3)",
        ]
        rc = uc.run_logged(argv, str(log), str(tmp_path), uc.child_env())
        assert rc == 3
        text = log.read_text(encoding="utf-8")
        assert text.startswith("$ ") and "hello from the step" in text
        assert "hello from the step" in capsys.readouterr().out

    def test_a_command_that_cannot_start_exits_127(self, tmp_path):
        log = tmp_path / "step.log"
        rc = uc.run_logged(["no-such-tool-anywhere"], str(log), str(tmp_path), {})
        assert rc == 127
        assert "could not start" in log.read_text(encoding="utf-8")

    def test_children_see_the_repo_unbuffered_and_in_utf8(self, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/elsewhere", uc.REPO_ROOT]))
        env = uc.child_env()
        assert env["PYTHONPATH"].split(os.pathsep) == [uc.REPO_ROOT, "/elsewhere"]
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"
        # wsl.exe's own errors ("no distribution with the supplied name") are
        # UTF-16 without it: a NUL after every letter in the step's .log.
        assert env["WSL_UTF8"] == "1"


class TestLaneWarnings:
    def _warn(self, level, server="geniex"):
        args = ["serve", "--compute", "npu"] + (["--log", level] if level else [])
        return uc.lane_warnings({"server": server, "serve_args": args})

    def test_a_geniex_lane_logging_at_info_is_flagged(self):
        # --log info cost the v0.7.0 NPU lane 13.7 % of its decode rate.
        assert "13.7 %" in self._warn("info")[0]

    @pytest.mark.parametrize(
        "level, server",
        [("none", "geniex"), ("error", "geniex"), (None, "geniex"), ("info", "ollama")],
    )
    def test_nothing_else_is(self, level, server):
        assert self._warn(level, server) == []
