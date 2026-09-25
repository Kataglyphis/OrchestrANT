"""The viewer's tables and intervals, checked without Reflex and without a browser.

Every function under test is pure over a manifest dict, which is the point of
keeping `benchmark_data` free of the frontend extra: the arithmetic that decides
what a person reads is testable in the default environment.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from frontend.frontend import benchmark_data as bd

# The summariser `report manifest` writes each run's `speed` block with, loaded
# from its file: `import orchestrant` runs the package __init__, which needs
# loguru, and the viewer job has none (test_viewer_job). It imports nothing.
_SUMMARISER = importlib.util.spec_from_file_location(
    "speed_summary",
    Path(__file__).resolve().parents[3] / "orchestrant/benchmark/speed_summary.py",
)
assert _SUMMARISER is not None and _SUMMARISER.loader is not None
speed_summary = importlib.util.module_from_spec(_SUMMARISER)
_SUMMARISER.loader.exec_module(speed_summary)
# answers.py the same way: _think and _think_unknown mirror its row rules.
_ANSWERS = importlib.util.spec_from_file_location(
    "answers", Path(__file__).resolve().parents[3] / "orchestrant/benchmark/answers.py"
)
assert _ANSWERS is not None and _ANSWERS.loader is not None
answers = importlib.util.module_from_spec(_ANSWERS)
_ANSWERS.loader.exec_module(answers)
RESULTS = Path(__file__).resolve().parents[3] / "benchmarks" / "benchmark_results"


def result(**overrides):
    row = {
        "prompt_index": 0,
        "prompt_preview": "prompt",
        "tokens_per_sec": 50.0,
        "latency_s": 2.0,
        "cpu_percent": 50.0,
        "ram_used_gb": 4.0,
        "completion_tokens": 100,
        "prompt_tokens": 20,
    }
    row.update(overrides)
    return row


def manifest_config(label="ctx8192_tok256", **overrides):
    """A manifest entry as `report manifest` writes one, `speed` block included."""
    config = {
        "label": label,
        "kind": "throughput",
        "config": {"max_tokens": 256, "extra_params": {"num_ctx": 8192}},
        "results": [result()],
    }
    config.update(overrides)
    config.setdefault("speed", speed_summary.summarise(config["results"]))
    return config


class TestHardware:
    def test_rows_cover_every_documented_field(self):
        rows = bd.hardware_rows(
            {
                "os": "Linux",
                "os_release": "6.1",
                "architecture": "x86_64",
                "cpu_model": "Ryzen",
                "cpu_physical_cores": 8,
                "cpu_total_threads": 16,
                "ram_total_gb": 32,
                "in_container": True,
                "ollama_host": "http://localhost:11434",
            }
        )
        values = {row["label"]: row["value"] for row in rows}
        assert values["Cores / Threads"] == "8 cores / 16 threads"
        assert values["Container"] == "Yes"
        assert "GPU" not in values

    def test_a_gpu_record_becomes_a_row(self):
        rows = bd.hardware_rows(
            {
                "os": "Windows",
                "gpu": {
                    "vendor": "amd",
                    "name": "AMD Radeon RX 9070 XT",
                    "memory_total_mb": 16304,
                },
            }
        )
        values = {row["label"]: row["value"] for row in rows}
        assert values["GPU"] == "AMD Radeon RX 9070 XT (AMD, 15.9 GB)"

    def test_a_gpu_record_without_memory_still_names_the_card(self):
        rows = bd.hardware_rows({"gpu": {"vendor": "nvidia", "name": "RTX 4090"}})
        values = {row["label"]: row["value"] for row in rows}
        assert values["GPU"] == "RTX 4090 (NVIDIA)"

    def test_a_missing_record_renders_nothing(self):
        assert bd.hardware_rows(None) == []
        assert bd.missing_hardware(None) == []

    def test_the_incomplete_list_survives(self):
        assert bd.missing_hardware({"incomplete": ["ram_total_gb"]}) == ["ram_total_gb"]


class TestCorrectness:
    def test_no_probe_is_not_a_pass(self):
        summary = bd.correctness_summary([manifest_config()])
        assert summary["checked"] is False
        assert summary["headline"] == "not checked"

    def test_a_full_score_is_correct(self):
        config = manifest_config(
            correctness={
                "score": 2,
                "total": 2,
                "items": [
                    {"correct": True, "expected": "391", "answer_preview": "391"},
                ],
            }
        )
        summary = bd.correctness_summary([config])
        assert (summary["state"], summary["headline"]) == ("ok", "Correct")
        assert summary["rows"][0]["ok"] is True

    def test_half_right_is_degraded_and_less_is_broken(self):
        def with_ratio(score, total):
            return bd.correctness_summary(
                [
                    manifest_config(
                        correctness={"score": score, "total": total, "items": []}
                    )
                ]
            )["state"]

        assert with_ratio(1, 2) == "degraded"
        assert with_ratio(1, 4) == "broken"

    def test_a_failed_item_carries_its_error(self):
        config = manifest_config(
            correctness={
                "score": 0,
                "total": 1,
                "items": [
                    {"correct": False, "expected": "391", "error": "connection reset"},
                ],
            }
        )
        assert (
            bd.correctness_summary([config])["rows"][0]["answer"] == "connection reset"
        )


def probed(integrity, capability):
    """A manifest entry whose probe block records both kinds' counts."""
    items = [
        {"kind": kind, "correct": ok, "expected": "x", "answer_preview": "x"}
        for kind, (score, total) in (
            ("integrity", integrity),
            ("capability", capability),
        )
        for ok in [True] * score + [False] * (total - score)
    ]
    return manifest_config(
        correctness={
            "score": integrity[0] + capability[0],
            "total": integrity[1] + capability[1],
            "integrity": {"score": integrity[0], "total": integrity[1]},
            "capability": {"score": capability[0], "total": capability[1]},
            "items": items,
        }
    )


class TestCorrectnessByKind:
    """Only the probe's integrity items decide the banner's state.

    Llama-3.2-3B scored 3/6 on 2026-09-24 and the banner read Degraded with
    "wrong answers here usually mean broken kernels" -- every miss was a
    capability item (strawberry, 5 machines, 9.9 vs 9.11) on a healthy lane.
    """

    def test_capability_misses_leave_it_correct(self):
        summary = bd.correctness_summary([probed((2, 2), (1, 4))])
        assert (summary["state"], summary["score"], summary["total"]) == ("ok", 2, 2)
        assert summary["capability"] == "capability 1/4 -- not a kernel verdict"

    def test_an_integrity_miss_still_degrades_it(self):
        assert bd.correctness_summary([probed((5, 6), (4, 4))])["state"] == "degraded"

    def test_each_row_names_its_kind(self):
        rows = bd.correctness_summary([probed((1, 1), (0, 1))])["rows"]
        assert [(r["kind"], r["ok"]) for r in rows] == [
            ("integrity", True),
            ("capability", False),
        ]

    def test_a_block_without_kinds_is_judged_whole_as_before(self):
        config = manifest_config(correctness={"score": 3, "total": 6, "items": []})
        summary = bd.correctness_summary([config])
        assert (summary["state"], summary["total"]) == ("degraded", 6)
        assert summary["capability"] == ""


class TestWilson:
    def test_perfect_score_still_has_a_lower_bound(self):
        low, high = bd.wilson(27, 27)
        assert high == pytest.approx(1.0)
        assert low < 1.0

    def test_no_trials_is_the_full_range(self):
        assert bd.wilson(0, 0) == (0.0, 1.0)

    def test_more_trials_narrow_the_interval(self):
        narrow = bd.wilson(8, 10)
        wide = bd.wilson(4, 5)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


class TestScoredRows:
    def test_rows_carry_interval_and_sort_by_score(self):
        configs = [
            manifest_config(
                label="a",
                scored=[{"label": "m", "passed": 1, "total": 2, "effective_n": 2}],
            ),
            manifest_config(
                label="b",
                scored=[{"label": "m", "passed": 2, "total": 2, "effective_n": 2}],
            ),
        ]
        rows = bd.scored_rows(configs)
        assert [row["label"] for row in rows] == ["m", "m"]
        assert rows[0]["pct"] == 100 and rows[1]["pct"] == 50
        assert rows[0]["low"] <= rows[0]["high"]

    def test_scored_table_existence(self):
        assert bd.scored_table_exists([manifest_config()]) is False
        assert (
            bd.scored_table_exists(
                [manifest_config(scored=[{"passed": 1, "total": 1}])]
            )
            is True
        )


class TestComparisonAndCharts:
    def test_average_only_counts_results_carrying_the_metric(self):
        config = manifest_config(
            results=[result(ttft_s=1.0), result(ttft_s=None, decode_tok_per_sec=5.0)]
        )
        row = bd.comparison_rows([config])[0]
        assert row["ttft"] == "1.00"
        assert row["decode"] == "5.0"

    def test_a_config_without_the_metric_is_dropped_from_the_chart(self):
        series = bd.chart_series(
            [
                manifest_config(results=[result(ttft_s=0.5)]),
                manifest_config(label="old", results=[result(ttft_s=None)]),
            ],
            "ttft_s",
            2,
        )
        assert [point["name"] for point in series] == ["ctx8192_tok256"]

    def test_ctx_and_tok_are_read_out_of_the_label(self):
        row = bd.comparison_rows([manifest_config(label="ollama-ctx16384_tok4096")])[0]
        assert (row["ctx"], row["tok"]) == ("16384", "4096")

    def test_gpu_utilization_averages_only_over_prompts_that_have_it(self):
        config = manifest_config(
            results=[result(gpu_utilization_percent=80.0), result()]
        )
        row = bd.comparison_rows([config])[0]
        assert row["gpu"] == "80.0"


class TestSpeedComesFromTheManifest:
    """OPS-6: the viewer charted a mean of per-request rates as "overall" --
    18.3 tok/s for the tracked v070-npu-speed, whose own table printed 25.4
    under that name. Its speed figures are now the manifest's `speed` block,
    computed by the runner's summariser, and never re-averaged here."""

    def test_the_table_and_the_charts_read_the_block_not_the_rows(self):
        speed = {"overall_tok_s": 19.28, "decode_tok_s": 19.56, "ttft_s": 0.161}
        config = manifest_config(results=[result(ttft_s=9.0)], speed=speed)
        row = bd.comparison_rows([config])[0]
        assert (row["tps"], row["decode"], row["ttft"]) == ("19.3", "19.6", "0.16")
        charted = {
            key: bd.chart_series([config], key, 2)[0]["value"]
            for key in ("tokens_per_sec", "decode_tok_per_sec", "ttft_s")
        }
        assert charted == {
            "tokens_per_sec": 19.28,
            "decode_tok_per_sec": 19.56,
            "ttft_s": 0.16,
        }
        assert bd.summary_stats([config])["avg_tps"] == "19.3"

    def test_a_manifest_older_than_the_block_shows_dashes_not_a_second_average(self):
        config = manifest_config(results=[result(ttft_s=1.0)])
        del config["speed"]
        row = bd.comparison_rows([config])[0]
        assert (row["tps"], row["decode"], row["ttft"]) == ("-", "-", "-")
        assert bd.chart_series([config], "ttft_s", 2) == []
        # Fields with no headline are still charted from the rows.
        assert bd.chart_series([config], "cpu_percent", 1)[0]["value"] == 50.0


class TestDetail:
    def test_extra_params_come_first_and_managed_keys_are_hidden(self):
        config = manifest_config(
            config={
                "max_tokens": 256,
                "prompts_requested": 5,
                "extra_params": {"num_ctx": 8192},
            }
        )
        rows = bd.detail_rows(config)
        assert rows[0] == {"label": "num_ctx", "value": "8192"}
        assert [row["label"] for row in rows] == ["num_ctx", "max_tokens"]

    def test_per_prompt_rows_degrade_missing_metrics_to_dash(self):
        rows = bd.per_prompt_rows(manifest_config(results=[result(ttft_s=None)]))
        assert rows[0]["ttft"] == "-"
        assert rows[0]["answer"] == "2.0"
        assert rows[0]["gpu"] == "-"

    def test_per_prompt_rows_carry_gpu_utilization(self):
        rows = bd.per_prompt_rows(
            manifest_config(results=[result(gpu_utilization_percent=42.0)])
        )
        assert rows[0]["gpu"] == "42.0"

    def test_errors_are_listed_separately(self):
        config = manifest_config(
            results=[result(), {"prompt_index": 1, "error": "boom"}]
        )
        assert bd.per_prompt_rows(config)[0]["index"] == 0
        assert bd.prompt_errors(config) == [{"index": 1, "error": "boom"}]


class TestSummary:
    def test_requests_and_errors_are_counted_apart(self):
        configs = [
            manifest_config(results=[result(), result(prompt_index=1)]),
            manifest_config(label="b", results=[{"prompt_index": 0, "error": "x"}]),
        ]
        summary = bd.summary_stats(configs)
        assert summary["requests"] == 2
        assert summary["errors"] == 1
        # 100 tokens in 2 s; the all-error run has no rate and does not count.
        assert summary["avg_tps"] == "50.0"


class TestAnswersAreNotTimeToTheCap:
    """2026-09-24: six of nine CPU-lane rows never closed <think> inside 256
    tokens. The viewer averaged their 0.0 shares in (~29 % thinking for a run
    that was ~95 %) and ranked time to the cap as time to an answer."""

    def test_an_older_reports_unclosed_think_reads_as_all_thinking(self):
        rows = [
            result(thinking_char_share=0.0, content_preview="<think>\nOkay"),
            result(thinking_char_share=0.9, content_preview="<think>a</think>b"),
        ]
        row = bd.comparison_rows([manifest_config(results=rows)])[0]
        assert row["think"] == "95%"

    def test_a_cut_row_has_no_time_to_an_answer(self):
        rows = [
            result(answered=True, wall_s_to_answer=2.0),
            result(answered=False, wall_s_to_answer=None, latency_s=12.0),
        ]
        config = manifest_config(results=rows)
        # The mean covers the answered rows, so it says how many.
        assert bd.comparison_rows([config])[0]["answer"] == "2.0 (1/2)"
        # "cut", as the runner's table prints it: '-' would read as unmeasured.
        assert [r["answer"] for r in bd.per_prompt_rows(config)] == ["2.0", "cut"]

    def test_an_older_row_without_the_flag_keeps_its_latency(self):
        rows = bd.per_prompt_rows(manifest_config(results=[result()]))
        assert rows[0]["answer"] == "2.0"


class TestAThinkingShareNobodyCanRead:
    """A reply cut before any <think> marker may be all thinking: a Qwen3
    template opens the tag in the prompt. The runner records its share as
    null with a `thinking_share_note`; an older report stored 0.0, which the
    viewer reads back as unknown, as answers.row_thinking_share does.
    """

    UNKNOWN = {"answered": False, "finish_reason": "length"}

    def test_the_mean_says_how_many_it_could_not_count(self):
        rows = [
            result(thinking_char_share=0.9),
            result(**self.UNKNOWN, thinking_char_share=None, thinking_share_note="x"),
            result(**self.UNKNOWN, thinking_char_share=0.0),  # an older report
        ]
        row = bd.comparison_rows([manifest_config(results=rows)])[0]
        assert row["think"] == "90% (2 unknown)"

    def test_a_run_of_unknowns_is_not_a_dash(self):
        rows = [result(**self.UNKNOWN, thinking_char_share=0.0)]
        assert bd.comparison_rows([manifest_config(results=rows)])[0]["think"] == "?"

    def test_the_per_prompt_cell_tells_unknown_from_unmeasured(self):
        rows = [
            result(**self.UNKNOWN, thinking_char_share=0.0),
            result(answered=True, finish_reason="stop", thinking_char_share=0.0),
            result(),  # a report older than the field
        ]
        cells = [r["think"] for r in bd.per_prompt_rows(manifest_config(results=rows))]
        assert cells == ["?", "0%", "-"]


def _tracked_share_rows():
    """Every result row of every tracked report that records a thinking share."""
    rows = []
    for path in sorted(RESULTS.rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for item in doc.get("reports") or doc.get("results") or []:
            nested = item.get("results") if isinstance(item, dict) else None
            found = nested if isinstance(nested, list) else [item]
            rows.extend(
                r for r in found if isinstance(r, dict) and "thinking_char_share" in r
            )
    return rows


def _runner_cell(row):
    """The per-prompt Think cell answers.py's rule gives a row."""
    if answers.row_thinking_unknown(row):
        return "?"
    share = answers.row_thinking_share(row)
    return "-" if share is None else f"{100 * share:.0f}%"


def _viewer_cells(rows):
    """The per-prompt Think cells the viewer prints for these rows."""
    config = manifest_config(results=[result(**r) for r in rows])
    return [r["think"] for r in bd.per_prompt_rows(config)]


class TestTheViewerReadsAShareAsTheRunnerDoes:
    """The viewer mirrors answers.row_thinking_share and row_thinking_unknown,
    whose rule the runner's summary line prints: a drift reads one report two
    ways, and the tests above never saw a blank reply that stopped on its own.
    Checked on one row per arm of the rule and on every tracked row."""

    ARMS = [
        {"thinking_char_share": 0.0, "content_preview": "<think>\nOkay"},
        {"answered": True, "thinking_char_share": 0.0, "content_preview": "<think>"},
        {"answered": False, "finish_reason": "length", "thinking_char_share": 0.0},
        {"answered": False, "finish_reason": "stop", "thinking_char_share": 0.0},
        {"answered": False, "finish_reason": None, "thinking_char_share": 0.0},
        {"truncated": True, "thinking_char_share": 0.0},
        {"truncated": True, "thinking_char_share": 0.0, "thinking_share_note": None},
        {"passed": True, "gave_up": True, "thinking_char_share": 0.0},
        {"thinking_char_share": None, "thinking_share_note": "cut"},
        {"thinking_char_share": None},
        {"truncated": True, "thinking_char_share": 0.5},
        {},
    ]

    def test_every_arm_reads_alike(self):
        assert _viewer_cells(self.ARMS) == [_runner_cell(r) for r in self.ARMS]

    def test_every_tracked_row_reads_alike(self):
        if not RESULTS.is_dir():
            pytest.skip("benchmarks/benchmark_results not present")
        rows = [r for r in _tracked_share_rows() if "error" not in r]
        assert rows, "no tracked row records a thinking share: this test went blind"
        assert _viewer_cells(rows) == [_runner_cell(r) for r in rows]


class TestPerPromptLabFields:
    """2026-09-24: first answer, lane and other load, CPU-rail joules per request."""

    def test_the_new_fields_render(self):
        row = result(
            ttfa_s=15.181,
            lane_cores=7.63,
            other_cores=0.35,
            cpu_rail_j_per_token=0.7216,
            cpu_rail_net_j_per_token=0.6779,
        )
        rows = bd.per_prompt_rows(manifest_config(results=[row]))
        assert {k: rows[0][k] for k in ("ttfa", "lane", "other", "jtok", "jnet")} == {
            "ttfa": "15.18",
            "lane": "7.63",
            "other": "0.35",
            "jtok": "0.722",
            "jnet": "0.678",
        }

    def test_other_load_is_derived_for_an_older_row_and_says_so(self):
        # v070-npu-speed predates other_cores: 8 threads x 23 % busy - 0.89
        # lane cores leaves 0.95 cores of something else.
        row = result(cpu_percent=23.0, cpu_percent_method="window", lane_cores=0.89)
        config = manifest_config(results=[row], cpu_threads=8)
        assert bd.per_prompt_rows(config)[0]["other"] == "0.95*"

    def test_a_snapshot_cpu_reading_derives_nothing(self):
        # The before/after snapshots never saw the request; no load from them.
        row = result(cpu_percent_method="before/after snapshots", lane_cores=0.9)
        config = manifest_config(results=[row], cpu_threads=8)
        assert bd.per_prompt_rows(config)[0]["other"] == "-"

    def test_an_old_row_renders_dashes_not_zeros(self):
        row = bd.per_prompt_rows(manifest_config())[0]
        assert [row[k] for k in ("ttfa", "lane", "other", "jtok", "jnet")] == ["-"] * 5


class TestScoredCountsAreCounts:
    def test_effective_k_is_used_rather_than_a_rounded_ratio(self):
        config = manifest_config(
            kind="bench_tools",
            scored=[
                {
                    "label": "m",
                    "passed": 7,
                    "total": 11,
                    "effective_n": 4,
                    "effective_k": 2,
                }
            ],
        )
        row = bd.scored_rows([config])[0]
        assert row["pct"] == 50  # 2 of 4 cases, not round(7 * 4 / 11) = 3 of 4


class TestManifestLocation:
    """`cd frontend; reflex run` reads from frontend/: the README's paths are
    relative to the repository root, and until 2026-09-24 the viewer resolved
    them against the working directory, where the default never existed."""

    def test_a_relative_path_is_read_from_the_repository_root(self, tmp_path):
        root, cwd = tmp_path / "repo", tmp_path / "repo" / "frontend"
        cwd.mkdir(parents=True)
        path = bd.manifest_location(
            "benchmarks/benchmark_results/_manifest.json", cwd, root
        )
        assert path == root / "benchmarks" / "benchmark_results" / "_manifest.json"

    def test_a_path_that_exists_from_the_working_directory_wins(self, tmp_path):
        root, cwd = tmp_path / "repo", tmp_path / "repo" / "frontend"
        (cwd / "run").mkdir(parents=True)
        (cwd / "run" / "_manifest.json").write_text("{}")
        assert bd.manifest_location("run/_manifest.json", cwd, root) == (
            cwd / "run" / "_manifest.json"
        )

    def test_an_absolute_path_is_taken_as_given(self, tmp_path):
        target = tmp_path / "elsewhere" / "_manifest.json"
        assert bd.manifest_location(str(target), tmp_path, tmp_path / "r") == target
