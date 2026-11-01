"""ai-regression.yml's verdicts on recorded reports: no server, model or image needed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/linux/ai_regression_verdicts.py"
SPEC = importlib.util.spec_from_file_location("ai_regression_verdicts", SCRIPT)
assert SPEC
assert SPEC.loader
verdicts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verdicts)

CASE = {
    "name": "capital-of-france",
    "expected_ids": [504, 3575, 2],
    "must_contain": "Paris",
}


def report(*answers: tuple[str, str]) -> dict:
    """Return a contract report envelope holding these (id, answer) checks."""
    checks = [{"id": i, "answer": a, "evidence": f"{i} said {a}"} for i, a in answers]
    return {
        "benchmark": "bench_contract",
        "reports": [{"label": "m", "checks": checks}],
    }


def write(path: Path, doc: object) -> str:
    """Write JSON and return the path as text."""
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_ungraded_answers_pass_and_land_in_the_table() -> None:
    """Yes, no, inconclusive and skipped are recorded, never graded."""
    rows = report(("a", "yes"), ("b", "no"), ("c", "inconclusive"), ("d", "skipped"))
    table, errors = verdicts.grade({"m": verdicts.contract_checks(rows)})
    assert errors == []
    assert table[2:] == [
        "| `a` | yes |",
        "| `b` | no |",
        "| `c` | inconclusive |",
        "| `d` | skipped |",
    ]


def test_an_error_answer_is_named_with_its_model_and_evidence() -> None:
    """One error answer of one model is one failure line."""
    small = verdicts.contract_checks(report(("a", "yes"), ("b", "error")))
    tiny = verdicts.contract_checks(report(("a", "yes")))
    table, errors = verdicts.grade({"small": small, "tiny": tiny})
    assert errors == ["small/b: b said error"]
    assert "| `b` | error | - |" in table


def test_a_report_without_checks_is_unreadable() -> None:
    """An empty report cannot pass as a clean one."""
    with pytest.raises(ValueError, match="no contract checks"):
        verdicts.contract_checks({"reports": [{"checks": []}]})


def test_main_exits_one_on_an_error_and_writes_the_summary(tmp_path: Path) -> None:
    """The workflow's verdict: 1 on an error answer, 0 without, 2 on a malformed report."""
    bad = write(tmp_path / "contract-small.json", report(("a", "error")))
    good = write(tmp_path / "contract-tiny.json", report(("a", "yes")))
    summary = tmp_path / "summary.md"
    assert verdicts.main([good, "--summary", str(summary)]) == 0
    assert verdicts.main([bad, good, "--summary", str(summary)]) == 1
    assert "| Check | small | tiny |" in summary.read_text(encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert (
        verdicts.main([str(tmp_path / "broken.json"), "--summary", str(summary)]) == 2
    )


def test_the_replay_needs_the_exact_ids_and_the_text() -> None:
    """A changed token id or a missing answer word each fail the replay."""
    assert verdicts.replay_problems(CASE, [504, 3575, 2], "It is Paris.") == []
    assert verdicts.replay_problems(CASE, [504, 3576, 2], "It is Paris.") == [
        "capital-of-france: ids [504, 3576, 2] != expected [504, 3575, 2]"
    ]
    assert verdicts.replay_problems(CASE, [504, 3575, 2], "It is Lyon.") == [
        "capital-of-france: 'It is Lyon.' lacks 'Paris'"
    ]


def test_percentile_rows_carry_p50_and_p95() -> None:
    """The row bench-history reads: the median and the inclusive 95th percentile."""
    row = verdicts.percentile_row("detector_two_cats", [1.0, 2.0, 3.0, 4.0])
    assert (row["p50_ms"], row["better"], row["rounds"]) == (2.5, "lower", 4)
    assert row["p95_ms"] == pytest.approx(3.85)
    with pytest.raises(ValueError, match="at least 2"):
        verdicts.percentile_row("x", [1.0])


def test_speed_rows_skip_failed_requests_and_convert_to_ms() -> None:
    """A failed request is no sample; seconds become milliseconds."""
    speed = {
        "results": [
            {"latency_s": 0.1, "ttft_s": 0.01},
            {"latency_s": 0.3, "ttft_s": 0.03},
            {"error": "HTTP 500", "latency_s": 9.0, "ttft_s": 9.0},
        ]
    }
    request, ttft = verdicts.speed_rows("smollm2", speed)
    assert (request["label"], request["p50_ms"]) == (
        "smollm2_request",
        pytest.approx(200.0),
    )
    assert (ttft["label"], ttft["p50_ms"]) == ("smollm2_ttft", pytest.approx(20.0))
