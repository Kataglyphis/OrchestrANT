"""A recount of the suspect cases that raises costs nothing measured: the rows are written, marked incomplete, exit 1."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_chat  # noqa: E402
import bench_tools  # noqa: E402
import compare_suspect  # noqa: E402
from orchestrant.benchmark import client as bench_cli  # noqa: E402
from orchestrant.benchmark import provenance  # noqa: E402

# The calibration endpoint, so the recount runs at all.
CONTROL = {
    "label": "control",
    "explicit_label": True,
    "backend": "control",
    "base_url": "http://c:1",
    "raw_base_url": None,
    "model": "q4",
    "entry": {},
}
ROW = {"case": "weather", "id": "weather", "passed": True, "wall_s": 0.5}


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


def _recount_raises(_reports):
    msg = "label"
    raise KeyError(msg)


def _drive(monkeypatch, tmp_path, module, argv):
    """Run one tool's main() over the control alone with a finished row; return its exit code and report."""
    out = tmp_path / "report.json"
    monkeypatch.setattr(bench_cli, "candidate_rows", lambda *a, **k: [dict(CONTROL)])
    outcome = {
        "label": "control",
        "backend": "control",
        "passed": 1,
        "total": 1,
        "deterministic": True,
        "total_wall_s": 0.5,
        "avg_wall_s": 0.5,
        "results": [dict(ROW)],
    }
    monkeypatch.setattr(module, "evaluate", lambda *a, **k: dict(outcome))
    monkeypatch.setattr(sys, "argv", [*argv, "--output", str(out)])
    code = module.main()
    return code, json.loads(out.read_text())


@pytest.mark.parametrize(
    ("module", "argv"),
    [(bench_tools, ["bench_tools.py"]), (bench_chat, ["bench_chat.py"])],
    ids=["tools", "chat"],
)
def test_a_raising_recount_still_writes_the_rows_and_exits_1(
    monkeypatch, tmp_path, host_load, capsys, module, argv
):
    monkeypatch.setattr(compare_suspect, "mark_suspect_cases", _recount_raises)
    code, report = _drive(monkeypatch, tmp_path, module, argv)
    assert code == 1
    assert "suspect_cases" in report["provenance"]["incomplete"]
    assert report["provenance"]["suspect_cases_error"] == "KeyError: 'label'"
    assert report["reports"][0]["results"] == [ROW]
    assert "suspect cases NOT marked (KeyError: 'label')" in capsys.readouterr().err


def test_a_recount_that_raises_halfway_leaves_every_row_as_measured(
    monkeypatch, capsys
):
    """The recount rewrites passed and total in place; a failure part-way must not leave half-recounted rows."""
    reports = [
        {
            "label": "control",
            "backend": "control",
            "results": [dict(ROW, passed=False)],
        },
        {
            "label": "lane",
            "backend": "geniex",
            "passed": 1,
            "total": 1,
            "results": [dict(ROW)],
        },
    ]
    before = json.loads(json.dumps(reports))

    def halfway(rows):
        rows[1]["passed"] = 0
        raise RuntimeError("boom")

    monkeypatch.setattr(compare_suspect, "mark_suspect_cases", halfway)
    suspect, extra = compare_suspect.mark_or_keep(reports)
    assert suspect is None
    assert reports == before
    assert extra == {
        "incomplete": ["suspect_cases"],
        "suspect_cases_error": "RuntimeError: boom",
    }
    capsys.readouterr()


def test_a_recount_that_works_adds_nothing():
    reports = [
        {
            "label": "lane",
            "backend": "geniex",
            "passed": 1,
            "total": 1,
            "results": [dict(ROW)],
        }
    ]
    suspect, extra = compare_suspect.mark_or_keep(reports)
    assert (suspect, extra) == (None, {})
