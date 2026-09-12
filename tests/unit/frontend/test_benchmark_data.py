"""The viewer's tables and intervals, checked without Reflex and without a browser.

Every function under test is pure over a manifest dict, which is the point of
keeping `benchmark_data` free of the frontend extra: the arithmetic that decides
what a person reads is testable in the default environment.
"""

from __future__ import annotations

import pytest

from frontend.frontend import benchmark_data as bd


def result(**overrides):
    row = {
        "prompt_index": 0,
        "prompt_preview": "prompt",
        "tokens_per_sec": 10.0,
        "latency_s": 2.0,
        "cpu_percent": 50.0,
        "ram_used_gb": 4.0,
        "completion_tokens": 100,
        "prompt_tokens": 20,
    }
    row.update(overrides)
    return row


def manifest_config(label="ctx8192_tok256", **overrides):
    config = {
        "label": label,
        "kind": "throughput",
        "config": {"max_tokens": 256, "extra_params": {"num_ctx": 8192}},
        "results": [result()],
    }
    config.update(overrides)
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
        assert summary["avg_tps"] == "10.0"
