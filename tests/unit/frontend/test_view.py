"""The Flet page, built without a client: what each card shows, and when it is left out.

Flet controls are plain Python objects, so the whole page is built and read here;
the Reflex page could only be checked by compiling it. Skipped where Flet is not
installed (the free-threaded and riscv64 legs sync only the `test` extra).
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest


ft = pytest.importorskip("flet")

from frontend.frontend import __main__ as entry  # noqa: E402
from frontend.frontend import benchmarks as page  # noqa: E402


def walk(node: Any) -> list[Any]:
    """Every control under `node`, depth first, through content, controls, rows, cells."""
    found: list[Any] = []
    if isinstance(node, (list, tuple)):
        for item in node:
            found += walk(item)
    elif isinstance(node, ft.BaseControl):
        found.append(node)
        for field in dataclasses.fields(node):
            if not field.name.startswith("_") and field.name != "parent":
                found += walk(getattr(node, field.name))
    return found


def texts(node: Any) -> list[str]:
    return [c.value for c in walk(node) if isinstance(c, ft.Text)]


def result(**fields):
    row = {
        "prompt_index": 0,
        "prompt_preview": "Explain a mutex",
        "prompt_tokens": 20,
        "completion_tokens": 100,
        "tokens_per_sec": 50.0,
        "latency_s": 2.0,
        "answered": True,
        "wall_s_to_answer": 2.0,
        "ttft_s": 0.2,
        "cpu_percent": 40.0,
        "ram_used_gb": 4.0,
        "lane_cores": 1.5,
    }
    row.update(fields)
    return row


def speed_run(label, overall, **fields):
    config = {
        "label": label,
        "kind": "throughput",
        "backend": "geniex-cpu",
        "config": {"max_tokens": 256, "extra_params": {"num_ctx": 8192}},
        "results": [result(), result(prompt_index=1, error="HTTP 500")],
        "speed": {"overall_tok_s": overall, "ttft_s": 0.2, "decode_tok_s": 55.0},
        "runtime": {"server": "ollama", "version": "0.12.3", "verified": True},
    }
    config.update(fields)
    return config


def full_manifest():
    """One of everything the page draws: hardware, a failed check, a score, a contract."""
    correctness = {
        "integrity": {"score": 1, "total": 2},
        "items": [
            {
                "kind": "integrity",
                "expected": "4",
                "answer_preview": "4",
                "correct": True,
            },
            {
                "kind": "integrity",
                "expected": "9",
                "answer_preview": "7",
                "correct": False,
            },
        ],
    }
    contract = {
        "label": "v070-cpu",
        "kind": "bench_contract",
        "unscored": [
            {
                "label": "geniex-cpu",
                "model": "m",
                "checks": [{"id": "t0", "answer": "yes"}],
            }
        ],
    }
    coding = {
        "label": "coding",
        "kind": "bench_coding",
        "scored": [{"label": "qwen", "passed": 8, "total": 10, "effective_k": 8}],
    }
    return {
        "title": "GenieX lanes",
        "model": "qwen3",
        "generated": "2026-10-19",
        "host_hardware": {"os": "Linux", "cpu_model": "x", "incomplete": ["gpu"]},
        "configs": [
            speed_run("ctx8192_tok256", 20.0, correctness=correctness),
            speed_run("ctx4096_tok256", 10.0),
            coding,
            contract,
        ],
    }


def test_every_card_renders_from_a_full_manifest():
    shown = texts(page.Viewer(full_manifest()).controls())
    for title in (
        "Hardware",
        "Correctness: Degraded — 1/2 integrity answers",
        "Scored runs — coding and tool calling",
        "Config Comparison",
        "Answers, load and energy",
        "Serving runtime",
        "Server contract",
        "Overall tok/s (whole request)",
        "Drill Down",
    ):
        assert title in shown
    assert "FAIL" in shown
    assert "8/10 = 80% [49–94%]" in shown
    assert any(t.startswith("Incomplete host record — missing: gpu") for t in shown)


def test_cards_without_data_are_left_out():
    shown = texts(page.Viewer({"configs": [speed_run("only", 5.0)]}).controls())
    assert "Correctness: not checked" in shown
    for absent in (
        "Hardware",
        "Scored runs — coding and tool calling",
        "Server contract",
    ):
        assert absent not in shown


def test_every_table_row_has_a_cell_per_column():
    # Flutter asserts this when it lays a DataTable out, long after Python built it.
    viewer = page.Viewer(full_manifest())
    viewer.select(0)
    tables = [c for c in walk(viewer.controls()) if isinstance(c, ft.DataTable)]
    assert len(tables) >= 8
    for grid in tables:
        assert all(len(row.cells) == len(grid.columns) for row in grid.rows)


def test_bars_scale_to_the_largest_value():
    spec = next(s for s in page.CHARTS if s[1] == "tokens_per_sec")
    card = page.chart_card(full_manifest()["configs"], spec)
    bars = [c.value for c in walk(card) if isinstance(c, ft.ProgressBar)]
    assert bars == [1.0, 0.5]
    assert "20.0 tok/s" in texts(card)


def test_a_metric_no_run_recorded_says_so_rather_than_drawing_zero():
    spec = next(s for s in page.CHARTS if s[1] == "gpu_utilization_percent")
    card = page.chart_card(full_manifest()["configs"], spec)
    assert not [c for c in walk(card) if isinstance(c, ft.ProgressBar)]
    assert any(
        t.startswith("No run recorded gpu_utilization_percent") for t in texts(card)
    )


def test_selecting_a_run_opens_and_closes_its_drill_down():
    viewer = page.Viewer(full_manifest())
    redraws = []
    viewer.on_change = lambda: redraws.append(viewer.selected)
    assert "Per-Prompt Results" not in texts(viewer.drill)
    viewer.select(0)
    shown = texts(viewer.drill)
    assert {"Per-Prompt Results", "Errors", "#1: HTTP 500", "num_ctx"} <= set(shown)
    viewer.select(0)
    assert "Per-Prompt Results" not in texts(viewer.drill)
    assert redraws == [0, -1]


def test_the_open_runs_button_is_the_filled_one():
    viewer = page.Viewer(full_manifest())
    viewer.select(1)
    buttons = [
        c
        for c in walk(viewer.drill)
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton))
    ]
    assert [type(b) is ft.FilledButton for b in buttons] == [False, True, False, False]


def test_a_missing_manifest_is_a_state_not_a_crash(tmp_path):
    manifest, error = page.load_manifest(tmp_path / "absent.json")
    assert manifest == {}
    assert error.startswith(str(tmp_path / "absent.json"))
    shown = texts(page.error_view(error))
    assert "Failed to load benchmark data" in shown
    assert any(page.MANIFEST_ENV in t for t in shown)


def test_a_manifest_that_is_not_an_object_is_an_error(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert page.load_manifest(path) == ({}, f"{path}: not a manifest (a JSON object)")


def test_the_web_server_builds_its_app():
    # flet-web 1.0.4 passes an argument FastAPI 0.145 removed; --web then dies on start.
    fastapi_app = pytest.importorskip("flet_web.fastapi")
    fastapi_app.FastAPI()


@pytest.mark.parametrize(
    ("argv", "view", "port"),
    [([], "FLET_APP", 0), (["--web", "--port", "8550"], "WEB_BROWSER", 8550)],
)
def test_the_command_line_picks_window_or_browser(monkeypatch, argv, view, port):
    calls = []
    monkeypatch.setattr(
        entry.ft, "run", lambda target, **kw: calls.append((target, kw))
    )
    entry.cli(argv)
    assert calls == [(page.main, {"view": getattr(ft.AppView, view), "port": port})]
