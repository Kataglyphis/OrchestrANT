"""Every lab report names its format, and both report readers refuse a format they do not know."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_compare  # noqa: E402
from orchestrant.benchmark import client, contract_cli  # noqa: E402

ENVELOPE = {"benchmark": "bench_tools", "provenance": {}, "config": {}, "reports": []}


def test_a_written_report_names_the_format(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "orchestrant.benchmark.provenance.collect_or_error", lambda *a, **k: {}
    )
    path = tmp_path / "tools.json"
    client.write_report(str(path), "bench_tools", {}, [], None, ())
    assert json.loads(path.read_text())["result_format"] == client.RESULT_FORMAT == 1


def test_bench_compare_refuses_a_newer_format_and_names_both_numbers(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({**ENVELOPE, "result_format": 2}))
    with pytest.raises(
        SystemExit,
        match=r"future\.json: result_format 2, but this checkout reads only 1",
    ):
        bench_compare.load(str(path))


def test_a_report_from_before_the_number_still_compares():
    assert bench_compare.normalise(dict(ENVELOPE))["benchmark"] == "bench_tools"
    assert (
        bench_compare.normalise({**ENVELOPE, "result_format": 1})["benchmark"]
        == "bench_tools"
    )


def test_contract_diff_refuses_a_newer_format(monkeypatch, tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps({"provenance": {}, "checks": {}}))
    new.write_text(json.dumps({"result_format": 7, "provenance": {}, "checks": {}}))
    monkeypatch.setattr(sys, "argv", ["contract", "--diff", str(old), str(new)])
    with pytest.raises(SystemExit, match=r"new\.json: result_format 7"):
        contract_cli.main()
