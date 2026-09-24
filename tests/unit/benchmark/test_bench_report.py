"""Tests for the summary/manifest/table code lifted out of run_benchmarks.sh.

It lived as three heredocs inside the shell script: unreachable from pytest,
un-lintable, and quoting-fragile. One had already grown a defensive comment
about a KeyError that "killed the whole comparison under set -e at the end of
every multi-hour run" — the kind of thing a five-line test catches before the
run instead of after.
"""

import json

from orchestrant.benchmark.report import (
    build_manifest,
    comparison_rows,
    result_files,
    summarise,
)


def write(tmp_path, name, doc):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return str(p)


LEGACY = {
    "model": "m",
    "config": {},
    "hardware": {"host": "h"},
    "results": [
        {
            "tokens_per_sec": 10.0,
            "latency_s": 2.0,
            "wall_s_to_answer": 2.0,
            "ttft_s": 0.5,
            "cpu_percent": 40.0,
            "ram_used_gb": 3.0,
            "completion_tokens": 100,
            "prompt_tokens": 20,
        },
        {
            "tokens_per_sec": 20.0,
            "latency_s": 4.0,
            "wall_s_to_answer": 4.0,
            "ttft_s": 1.5,
            "cpu_percent": 60.0,
            "ram_used_gb": 5.0,
            "completion_tokens": 200,
            "prompt_tokens": 30,
        },
        {"error": "boom"},
    ],
}


class TestSummarise:
    def test_averages_only_successful_results(self):
        s = summarise(LEGACY)
        assert s["n"] == 2
        assert s["tokens_per_sec"] == 15.0
        assert s["completion_tokens"] == 300

    def test_a_file_with_no_successes_returns_none(self):
        assert summarise({"results": [{"error": "x"}]}) is None

    def test_missing_ttft_is_none_not_zero(self):
        # Reporting 0.00s for "not measured" would claim an instant first token.
        doc = {"results": [{"tokens_per_sec": 5.0, "latency_s": 1.0}]}
        assert summarise(doc)["ttft_s"] is None

    def test_gpu_utilization_averages_over_the_prompts_that_carry_it(self):
        doc = {
            "results": [
                {
                    "tokens_per_sec": 5.0,
                    "latency_s": 1.0,
                    "gpu_utilization_percent": 80.0,
                },
                {
                    "tokens_per_sec": 5.0,
                    "latency_s": 1.0,
                    "gpu_utilization_percent": 20.0,
                },
                {"tokens_per_sec": 5.0, "latency_s": 1.0},
            ]
        }
        assert summarise(doc)["gpu_utilization_percent"] == 50.0

    def test_reads_the_newer_envelope_too(self):
        doc = {
            "reports": [
                {
                    "results": [
                        {
                            "tokens_per_sec": 8.0,
                            "latency_s": 1.0,
                            "cpu_percent": 1.0,
                            "ram_used_gb": 1.0,
                        }
                    ]
                }
            ]
        }
        assert summarise(doc)["n"] == 1


class TestResultFileSelection:
    def test_skips_generated_files(self, tmp_path):
        # _manifest.json has no `results` key and sorts FIRST; without this
        # guard the KeyError killed the comparison at the end of a long run.
        write(tmp_path, "_manifest.json", {"configs": []})
        write(tmp_path, "a.json", LEGACY)
        files = result_files(str(tmp_path))
        assert len(files) == 1 and files[0].endswith("a.json")

    def test_an_empty_directory_is_not_an_error(self, tmp_path):
        assert result_files(str(tmp_path)) == []


class TestManifest:
    def test_lists_every_result_file(self, tmp_path):
        write(tmp_path, "a.json", LEGACY)
        write(tmp_path, "b.json", LEGACY)
        m = build_manifest(str(tmp_path), "T", "m", "now")
        assert [c["label"] for c in m["configs"]] == ["a", "b"]

    def test_takes_hardware_from_the_first_file_that_has_it(self, tmp_path):
        write(tmp_path, "a.json", {"results": [], "hardware": {}})
        write(tmp_path, "b.json", LEGACY)
        assert build_manifest(str(tmp_path), "T", "m", "now")["host_hardware"] == {
            "host": "h"
        }

    def test_accepts_provenance_as_hardware(self, tmp_path):
        # The newer tools record `provenance`, not `hardware`.
        write(tmp_path, "a.json", {"reports": [], "provenance": {"host": "z"}})
        assert build_manifest(str(tmp_path), "T", "m", "now")["host_hardware"] == {
            "host": "z"
        }

    def test_survives_a_manifest_already_in_the_directory(self, tmp_path):
        write(tmp_path, "_manifest.json", {"configs": []})
        write(tmp_path, "a.json", LEGACY)
        m = build_manifest(str(tmp_path), "T", "m", "now")
        assert len(m["configs"]) == 1


class TestComparisonTable:
    def test_one_row_per_result_file(self, tmp_path):
        write(tmp_path, "a.json", LEGACY)
        write(tmp_path, "_manifest.json", {"configs": []})
        rows = comparison_rows(str(tmp_path))
        assert len(rows) == 1 and rows[0][0] == "a"

    def test_files_without_successes_are_skipped_not_crashed_on(self, tmp_path):
        write(tmp_path, "empty.json", {"results": [{"error": "x"}]})
        write(tmp_path, "a.json", LEGACY)
        assert [r[0] for r in comparison_rows(str(tmp_path))] == ["a"]


ENVELOPE = {
    "benchmark": "bench_tools",
    "provenance": {"host": "h"},
    "config": {},
    "reports": [
        {
            "label": "m",
            "model": "m",
            "passed": 25,
            "total": 27,
            "results": [{"case": "a", "passed": True}],
        }
    ],
}


class TestScoredRowsAreCounts:
    """D31: a `scored` row with no integer passed/total rendered as
    '/ = 0% [0-100%]' and ranked last. turn_growth and embeddings write rows
    of a different shape; they are not scores.
    """

    def test_a_scored_row_needs_integer_passed_and_total(self, tmp_path):
        write(tmp_path, "t.json", ENVELOPE)
        m = build_manifest(str(tmp_path), "T", "m", "now")
        assert m["configs"][0]["kind"] == "bench_tools"
        assert [r["passed"] for r in m["configs"][0]["scored"]] == [25]

    def test_turn_growth_has_no_scored_rows(self, tmp_path):
        doc = {
            "benchmark": "bench_tools_turn_growth",
            "provenance": {},
            "config": {},
            "reports": [{"label": "m", "model": "m", "results": [{"turn": 1}]}],
        }
        write(tmp_path, "g.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert "scored" not in entry
        assert entry["kind"] == "bench_tools_turn_growth"
        assert entry["unscored"][0]["label"] == "m"
        assert "results" not in entry["unscored"][0]

    def test_embeddings_semantic_score_is_not_a_scored_row(self, tmp_path):
        doc = {
            "benchmark": "bench_embeddings",
            "provenance": {},
            "config": {},
            "reports": [
                {
                    "label": "e",
                    "model": "e",
                    "semantic_passed": 3,
                    "semantic_total": 3,
                    "deterministic": True,
                }
            ],
        }
        write(tmp_path, "e.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert "scored" not in entry and entry["unscored"][0]["semantic_passed"] == 3

    def test_booleans_are_not_counts(self, tmp_path):
        doc = dict(ENVELOPE, reports=[{"label": "m", "passed": True, "total": True}])
        write(tmp_path, "b.json", doc)
        assert (
            "scored" not in build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        )

    def test_a_mixed_envelope_keeps_only_the_real_scores(self, tmp_path):
        doc = dict(
            ENVELOPE,
            reports=ENVELOPE["reports"]
            + [{"label": "x", "passed": None, "total": None}],
        )
        write(tmp_path, "mix.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert [r["label"] for r in entry["scored"]] == ["m"]
        assert [r["label"] for r in entry["unscored"]] == ["x"]


class TestReportKind:
    def test_lanes_envelope_is_labelled_by_its_benchmark(self, tmp_path):
        doc = {
            "benchmark": "bench_lanes",
            "provenance": {},
            "config": {},
            "reports": [{"label": "npu", "tok_per_sec": 19.0}],
        }
        write(tmp_path, "l.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["kind"] == "bench_lanes" and "scored" not in entry

    def test_legacy_shape_is_throughput(self, tmp_path):
        write(tmp_path, "a.json", LEGACY)
        assert (
            build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]["kind"]
            == "throughput"
        )

    def test_a_json_that_is_no_report_is_unknown_and_warned(self, tmp_path, capsys):
        # The envelope-less dict bench_lanes used to write: indexed as an empty
        # 'throughput' run, silently.
        write(tmp_path, "stray.json", {"prompt": "x", "max_tokens": 256})
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["kind"] == "unknown"
        assert "stray.json" in capsys.readouterr().err


RUNTIME = {
    "server": "geniex",
    "cli": "v0.7.0",
    "qairt": "2.45",
    "llama_cpp": "4ff829e",
    "serve_args": ["serve", "--compute", "cpu", "--log", "none"],
    "verified": True,
}


class TestManifestCarriesWhatTheViewerShows:
    """2026-09-24: the manifest carried the first file's hardware and nothing
    per run, so the viewer could not tell a v0.6.1 run from a v0.7.0 one, nor
    a `--log info` lane from a `--log none` one.
    """

    def test_a_speed_report_carries_runtime_energy_and_threads(self, tmp_path):
        energy = {"available": True, "idle_drift_w": 0.893, "net_reliable": False}
        doc = dict(
            LEGACY,
            backend="geniex-cpu",
            api_url="http://127.0.0.1:18184/v1",
            hardware={"cpu_total_threads": 8},
            provenance={"base_url": "http://127.0.0.1:18184", "runtime": RUNTIME},
            energy=energy,
        )
        write(tmp_path, "s.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["runtime"] == RUNTIME
        assert entry["energy"] == energy
        assert (entry["backend"], entry["model"], entry["cpu_threads"]) == (
            "geniex-cpu",
            "m",
            8,
        )
        assert entry["base_url"] == "http://127.0.0.1:18184"

    def test_a_contract_report_keeps_its_checks_and_runtime(self, tmp_path):
        check = {"id": "seed_deterministic", "answer": "yes", "evidence": "same"}
        doc = {
            "benchmark": "bench_contract",
            "provenance": {"base_url": "http://127.0.0.1:18181", "runtime": RUNTIME},
            "config": {"prefix_tokens": 2000},
            "reports": [{"label": "geniex-npu", "model": "q", "checks": [check]}],
        }
        write(tmp_path, "c.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["kind"] == "bench_contract" and "scored" not in entry
        assert entry["unscored"][0]["checks"] == [check]
        assert entry["runtime"] == RUNTIME

    def test_an_older_report_carries_nones_not_a_crash(self, tmp_path):
        write(tmp_path, "a.json", {"results": []})
        write(tmp_path, "b.json", {"reports": [], "provenance": {"error": "boom"}})
        write(tmp_path, "c.json", {"reports": [], "provenance": "not a dict"})
        for entry in build_manifest(str(tmp_path), "T", "m", "now")["configs"]:
            assert entry["runtime"] is None and entry["energy"] is None
            assert entry["cpu_threads"] is None and entry["base_url"] is None

    def test_the_legacy_url_stands_in_for_a_missing_provenance(self, tmp_path):
        doc = dict(LEGACY, api_url="http://h:11434/v1")
        write(tmp_path, "a.json", doc)
        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["base_url"] == "http://h:11434/v1"


class TestAnswerCarriesItsCount:
    def test_a_run_with_cut_replies_says_how_many_answered(self):
        from orchestrant.benchmark.report import _answer, summarise

        doc = {
            "results": [
                {"answered": True, "wall_s_to_answer": 0.7, "latency_s": 0.7},
                {"answered": False, "wall_s_to_answer": None, "latency_s": 11.5},
            ]
        }
        s = summarise(doc)
        assert s["answered"] == (1, 2)
        assert _answer(s, ".1f") == "0.7s (1/2 answered)"
