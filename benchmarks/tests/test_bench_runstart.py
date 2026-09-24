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


def _drive(monkeypatch, tmp_path, module, argv):
    """Run a candidate-based tool's main() and return its report's provenance."""
    out = tmp_path / "report.json"
    monkeypatch.setattr(bench_cli, "candidate_rows", lambda *a, **k: [dict(LANE)])
    monkeypatch.setattr(module, "evaluate", lambda *a, **k: dict(OUTCOME))
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
    def test_hashes_the_probe_not_the_plumbing(self, monkeypatch, tmp_path, host_load):
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
            prov = _drive(
                monkeypatch, tmp_path, bc, ["bench_coding.py", "--task-set", "classic"]
            )
        finally:
            bc.TASKS = original
        _assert_started(prov, host_load, ["bench_coding.py", "determinism.py"])


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
        _assert_started(prov, host_load, ["bench_agent.py"])
