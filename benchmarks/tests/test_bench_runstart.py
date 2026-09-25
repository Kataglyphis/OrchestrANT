"""Every lab tool takes a run-start record and hands it to its report.

SRC-1: only the speed runner hashed its source when it started, so a grader
edited mid-run left the other tools' reports naming code that did not produce
their first rows. P1.5: no report said how busy the machine was. OPS-9: every
tool hashed provenance.py, so each edit to that plumbing read as "the grader
changed". These drive each main() with the network and the grading stubbed
and read what reached the report.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_embeddings as be  # noqa: E402
import bench_tools as bt  # noqa: E402
import compare_suspect  # noqa: E402
from orchestrant.benchmark import client as bench_cli  # noqa: E402
from orchestrant.benchmark import provenance  # noqa: E402

LANE = {
    "label": "lane",
    "explicit_label": True,
    "backend": "geniex",
    "base_url": "http://lane:1",
    "raw_base_url": None,
    "model": "q4",
    "entry": {},
}
# The calibration endpoint: backend "control" in backends.json.
CONTROL = {**LANE, "label": "control", "backend": "control", "base_url": "http://c:1"}

OUTCOME = {
    "label": "lane",
    "backend": "geniex",
    "passed": 0,
    "total": 0,
    "deterministic": False,
    "total_wall_s": 0.0,
    "avg_wall_s": None,
    "results": [],
}

# Plumbing: an edit to either must never read as "the grader changed".
PLUMBING = {"client.py", "provenance.py"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(provenance, "busy_lanes", lambda *a, **k: [])
    monkeypatch.setattr(provenance, "_server_models", lambda *a, **k: None)
    monkeypatch.setattr(provenance, "runtime_info", lambda *a, **k: None)
    monkeypatch.setattr(
        provenance,
        "determinism_probe",
        lambda *a, **k: {"deterministic": True, "requests": 3, "error": None},
    )


def _drive(monkeypatch, tmp_path, module, argv, lane=LANE):
    """Run a candidate-based tool's main() over `lane` alone and return its
    report's provenance."""
    out = tmp_path / "report.json"
    monkeypatch.setattr(bench_cli, "candidate_rows", lambda *a, **k: [dict(lane)])
    named = {"label": lane["label"], "backend": lane["backend"]}
    monkeypatch.setattr(module, "evaluate", lambda *a, **k: {**OUTCOME, **named})
    monkeypatch.setattr(sys, "argv", [*argv, "--output", str(out)])
    module.main()
    return json.loads(out.read_text())["provenance"]


def _assert_started(prov, host_load, files, lane="http://lane:1"):
    assert prov["tool_files"] == sorted(files)
    assert not PLUMBING & set(prov["tool_files"])
    # Checked and unchanged -- the start hash was taken over the same files.
    assert prov["source_changed_during_run"] is False
    assert prov["host_load"]["note"] == "stubbed by conftest"
    assert prov["run_started_utc"]
    assert host_load == [{"seconds": 3, "lane": lane}]


class TestBenchTools:
    def test_the_case_suite_hashes_the_probe_not_the_plumbing(
        self, monkeypatch, tmp_path, host_load
    ):
        prov = _drive(monkeypatch, tmp_path, bt, ["bench_tools.py"])
        files = ["bench_tools.py", "tools_opencode.py", "determinism.py"]
        _assert_started(prov, host_load, files)

    def test_accept_text_json_hashes_the_parser_that_salvages_calls(
        self, monkeypatch, tmp_path, host_load
    ):
        # Under the flag the shim's parse_tool_calls decides which prose
        # answers pass, so an edit to it moves the score.
        argv = ["bench_tools.py", "--accept-text-json"]
        prov = _drive(monkeypatch, tmp_path, bt, argv)
        files = ["bench_tools.py", "tools_opencode.py", "determinism.py"]
        _assert_started(prov, host_load, [*files, "geniex_toolcall_shim.py"])

    def test_prompt_variants_hashes_the_arithmetic_of_the_sample(
        self, monkeypatch, tmp_path, host_load
    ):
        # bench_variants.py decides effective_n/k and the spread under the
        # flag; it left bench_tools.py, whose hash covered it until then.
        argv = ["bench_tools.py", "--prompt-variants"]
        prov = _drive(monkeypatch, tmp_path, bt, argv)
        files = ["bench_tools.py", "tools_opencode.py", "determinism.py"]
        _assert_started(prov, host_load, [*files, "bench_variants.py"])

    def test_a_control_hashes_the_recount_of_every_other_row(
        self, monkeypatch, tmp_path, host_load
    ):
        # mark_suspect_cases() takes the cases the control fails out of every
        # other row's passed/total/effective_n before the write: with a
        # control, an edit to compare_suspect.py moves a score.
        prov = _drive(monkeypatch, tmp_path, bt, ["bench_tools.py"], lane=CONTROL)
        files = ["bench_tools.py", "tools_opencode.py", "determinism.py"]
        _assert_started(prov, host_load, [*files, "compare_suspect.py"], "http://c:1")

    def test_turn_growth_runs_no_probe_and_does_not_hash_it(
        self, monkeypatch, tmp_path, host_load
    ):
        # --tools opencode feeds the loop tools_opencode's schemas, so that
        # file decides this report as much as the case suite's.
        monkeypatch.setattr(bt, "turn_growth", lambda *a, **k: [])
        prov = _drive(monkeypatch, tmp_path, bt, ["bench_tools.py", "--turn-growth"])
        _assert_started(prov, host_load, ["bench_tools.py", "tools_opencode.py"])


class TestBenchEmbeddings:
    def test_takes_a_start_and_hashes_only_itself(
        self, monkeypatch, tmp_path, host_load
    ):
        out = tmp_path / "emb.json"
        monkeypatch.setattr(
            bench_cli,
            "resolve_candidates",
            lambda *a, **k: [("emb", "http://emb:1", "m", {})],
        )
        monkeypatch.setattr(be, "run", lambda *a, **k: {"label": "emb"})
        monkeypatch.setattr(sys, "argv", ["bench_embeddings.py", "--output", str(out)])
        be.main()
        prov = json.loads(out.read_text())["provenance"]
        _assert_started(prov, host_load, ["bench_embeddings.py"], lane="http://emb:1")


class TestBenchCoding:
    @pytest.mark.parametrize(
        ("flags", "lane", "tables"),
        [
            (["--task-set", "classic"], LANE, []),
            (["--task-set", "novel"], LANE, []),
            (["--task-set", "all"], LANE, ["bench_tasks.py"]),
            (
                ["--task-set", "classic", "--prompt-variants"],
                LANE,
                ["bench_variants.py"],
            ),
            (["--task-set", "classic"], CONTROL, ["compare_suspect.py"]),
        ],
    )
    def test_hashes_the_probe_and_the_tasks_not_the_plumbing(
        self, monkeypatch, tmp_path, host_load, flags, lane, tables
    ):
        # bench_tasks.py holds the extended and language sets -- prompts and
        # the tests that grade them. The default set ("all") runs 21 of them.
        # bench_variants.py counts the sample under --prompt-variants; until
        # it had its own module, no file in a coding report's hash did.
        # compare_suspect.py recounts every other row once a control runs.
        pytest.importorskip("resource")  # bench_coding's sandbox is Linux-only
        import bench_coding as bc

        monkeypatch.setattr(
            bc,
            "grader_selfcheck",
            lambda tasks: {
                "tasks": 0,
                "checked": 0,
                "passed": True,
                "netns": False,
                "seconds": 0.0,
                "tools": {},
                "skipped": {},
            },
        )
        original = bc.TASKS
        try:
            argv = ["bench_coding.py", *flags]
            prov = _drive(monkeypatch, tmp_path, bc, argv, lane=lane)
        finally:
            bc.TASKS = original
        files = ["bench_coding.py", "determinism.py", *tables]
        _assert_started(prov, host_load, files, lane=lane["base_url"])


class TestBenchChat:
    def test_a_control_hashes_the_recount_of_every_other_row(
        self, monkeypatch, tmp_path, host_load
    ):
        # Without a control the fingerprint is TOOL_FILES (test_bench_chat).
        import bench_chat as bc

        prov = _drive(monkeypatch, tmp_path, bc, ["bench_chat.py"], lane=CONTROL)
        files = ["bench_chat.py", "determinism.py", "compare_suspect.py"]
        _assert_started(prov, host_load, files, lane="http://c:1")


class TestTheSuspectFileJoinsOnlyWithAControl:
    """compare_suspect.suspect_tool_files: the recount decides a score only
    when a control ran; without one it changes nothing, and hashing it would
    call an edit to it a grader change in reports it never touched (OPS-9)."""

    def test_no_control_adds_nothing(self):
        assert compare_suspect.suspect_tool_files([LANE]) == ()

    @pytest.mark.parametrize(
        "control",
        [CONTROL, {**LANE, "label": "control-hosted"}],
        ids=["backend", "label"],
    )
    def test_a_control_adds_the_module_itself(self, control):
        # Either way is_control() names a control, so the recount runs.
        own = os.path.abspath(compare_suspect.__file__)
        assert compare_suspect.suspect_tool_files([LANE, control]) == (own,)


class TestBenchAgent:
    def test_takes_a_start_on_the_opencode_lane(self, monkeypatch, tmp_path, host_load):
        import bench_agent as ba

        cfg = tmp_path / "opencode.jsonc"
        cfg.write_text(
            '{"provider": {"lane": {"options": {"baseURL": "http://lane:1/v1"}}}}'
        )
        monkeypatch.setenv("OPENCODE_CONFIG", str(cfg))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        monkeypatch.setattr(ba, "OPENCODE", sys.executable)
        monkeypatch.setattr(ba, "opencode_version", lambda: "0.0-test")
        monkeypatch.setattr(ba, "missing_tools", lambda task: [])
        monkeypatch.setattr(
            ba,
            "run_task",
            lambda task, *a, **k: {
                "task": task["name"],
                "passed": True,
                "blocked": False,
                "wall_s": 1.0,
            },
        )
        out = tmp_path / "agent.json"
        argv = ["bench_agent.py", "--model", "lane/m", "--output", str(out)]
        monkeypatch.setattr(sys, "argv", argv)
        ba.main()
        prov = json.loads(out.read_text())["provenance"]
        # The medium fixture is graded code too (agent-repeats), so it is hashed.
        files = [
            "bench_agent.py",
            "bench_agent_medium.py",
            "bench_agent_medium_files.py",
        ]
        _assert_started(prov, host_load, files)
