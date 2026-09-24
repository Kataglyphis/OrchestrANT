"""The lab's 2026-09-24 fields as the viewer shows them, checked without Reflex.

Answers, load, CPU-rail energy and the serving runtime per run, and the
contract probe as a check x run grid. Figures in the fixtures are the ones
benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md quotes, so a test that
drifts from the page's arithmetic says which number it would misprint.
"""

from __future__ import annotations

from frontend.frontend import lab_data as ld


def row(**fields):
    base = {"prompt_index": 0, "completion_tokens": 100, "latency_s": 2.0}
    base.update(fields)
    return base


def speed_config(results, **fields):
    config = {"label": "run", "kind": "throughput", "results": results}
    config.update(fields)
    return config


def lab_row(results, **fields):
    return ld.lab_rows([speed_config(results, **fields)])[0]


class TestAnswers:
    def test_answered_counts_and_first_answer_covers_answered_rows_only(self):
        rows = [
            row(answered=True, ttfa_s=10.0),
            row(answered=True, ttfa_s=20.0),
            # A cut reply may have started answering; nobody received that answer.
            row(answered=False, ttfa_s=1.0),
        ]
        out = lab_row(rows)
        assert (out["answered"], out["cut"], out["ttfa"]) == ("2/3", True, "15.00")

    def test_all_answered_is_not_cut(self):
        out = lab_row([row(answered=True, ttfa_s=0.14)])
        assert (out["answered"], out["cut"]) == ("1/1", False)

    def test_a_report_older_than_the_flag_shows_a_dash(self):
        out = lab_row([row(lane_cores=0.9)])
        assert (out["answered"], out["ttfa"]) == ("-", "-")

    def test_thinking_share_repairs_an_unclosed_think(self):
        rows = [
            row(lane_cores=7.0, thinking_char_share=0.0, content_preview="<think>\nOk"),
            row(lane_cores=7.0, thinking_char_share=0.9),
        ]
        assert lab_row(rows)["think"] == "95%"


class TestLoad:
    def test_lane_mean_and_other_mean_with_max(self):
        rows = [
            row(lane_cores=7.4, other_cores=0.30),
            row(lane_cores=7.6, other_cores=0.66),
        ]
        out = lab_row(rows)
        assert (out["lane_cores"], out["other_cores"]) == ("7.50", "0.48 (max 0.66)")

    def test_an_older_report_derives_other_load_and_marks_it(self):
        rows = [row(cpu_percent=100.0, cpu_percent_method="window", lane_cores=7.2)]
        assert lab_row(rows, cpu_threads=8)["other_cores"] == "0.80 (max 0.80)*"

    def test_without_the_thread_count_nothing_is_derived(self):
        rows = [row(cpu_percent=100.0, cpu_percent_method="window", lane_cores=7.2)]
        assert lab_row(rows)["other_cores"] == "-"

    def test_zero_other_load_is_a_reading_not_a_gap(self):
        assert lab_row([row(lane_cores=0.9, other_cores=0.0)])["other_cores"] == (
            "0.00 (max 0.00)"
        )


class TestEnergy:
    def test_joules_per_token_is_a_ratio_of_sums(self):
        # 8 tokens at 0.5 J/token and 256 at 0.1: the mean of ratios reads 0.3,
        # the ratio of sums 29.6 / 264 = 0.112 -- what the runner prints.
        rows = [
            row(completion_tokens=8, cpu_rail_energy_j=4.0, cpu_rail_window_s=1.0),
            row(completion_tokens=256, cpu_rail_energy_j=25.6, cpu_rail_window_s=9.0),
        ]
        out = lab_row(rows)
        assert (out["j_gross"], out["watts"]) == ("0.112", "3.0")

    def test_net_only_when_every_metered_row_has_it(self):
        rows = [
            row(cpu_rail_energy_j=10.0, cpu_rail_net_energy_j=5.0),
            row(cpu_rail_energy_j=10.0),
        ]
        assert lab_row(rows)["j_net"] == "-"
        rows[1]["cpu_rail_net_energy_j"] = 7.0
        assert lab_row(rows)["j_net"] == "0.060"

    def test_power_falls_back_to_latency_for_an_older_row(self):
        rows = [row(cpu_rail_energy_j=10.0, latency_s=4.0)]
        assert lab_row(rows)["watts"] == "2.5"

    def test_an_unmetered_run_renders_dashes(self):
        out = lab_row([row(lane_cores=0.9)])
        assert (out["j_gross"], out["j_net"], out["watts"]) == ("-", "-", "-")


class TestNetReliability:
    def test_a_drifted_baseline_says_read_gross(self):
        # v070r2-cpu-speed-answer: 1.597 W before, 0.704 W after.
        out = ld.net_reliability(
            {"available": True, "idle_drift_w": 0.893, "net_reliable": False}
        )
        assert (out["net_state"], out["net_text"]) == ("drifted", "DRIFTED 0.89 W")
        assert "read gross" in out["net_note"]

    def test_a_steady_baseline_is_reliable(self):
        out = ld.net_reliability(
            {"available": True, "idle_drift_w": 0.05, "net_reliable": True}
        )
        assert (out["net_state"], out["net_text"]) == ("reliable", "steady")

    def test_one_baseline_is_unknown_not_reliable(self):
        out = ld.net_reliability({"available": True, "net_reliable": None})
        assert out["net_state"] == "unknown"

    def test_a_report_older_than_the_flag_is_unknown(self):
        # v061-npu-speed: rows netted against one 5-s baseline, no drift known.
        out = ld.net_reliability({"available": True, "rails": ["CPU_CLUSTER_0"]})
        assert (out["net_state"], out["net_text"]) == ("unknown", "unknown")

    def test_no_meter_names_the_reason(self):
        out = ld.net_reliability({"available": False, "reason": "not Windows"})
        assert (out["net_state"], out["net_note"]) == ("none", "not Windows")

    def test_no_block_at_all(self):
        assert ld.net_reliability(None)["net_text"] == "-"

    def test_the_flag_reaches_the_lab_row(self):
        energy = {"available": True, "idle_drift_w": 0.3, "net_reliable": False}
        assert lab_row([row(lane_cores=0.9)], energy=energy)["net_state"] == "drifted"


class TestLabRowSelection:
    def test_runs_without_any_lab_field_get_no_row(self):
        coding = speed_config([{"task": "fizzbuzz", "thinking_char_share": 0.0}])
        speed = speed_config([row(answered=True)], label="speed")
        assert [r["label"] for r in ld.lab_rows([coding, speed])] == ["speed"]

    def test_errored_rows_are_left_out(self):
        rows = [row(answered=True), {"prompt_index": 1, "error": "boom"}]
        assert lab_row(rows)["answered"] == "1/1"


GENIEX_CPU = {
    "server": "geniex",
    "cli": "v0.7.0",
    "qairt": "2.45",
    "llama_cpp": "4ff829e",
    "serve_args": [
        "serve",
        "--compute",
        "cpu",
        "--host",
        "127.0.0.1:18184",
        "--nctx",
        "16384",
        "--log",
        "none",
    ],
    "verified": True,
    "source": "lane process pid 19712: geniex.exe --version",
}


class TestRuntime:
    def test_a_geniex_build_is_named_in_full(self):
        assert ld.runtime_label(GENIEX_CPU) == (
            "geniex v0.7.0 (QAIRT 2.45, llama.cpp 4ff829e)"
        )

    def test_other_servers_and_missing_runtimes(self):
        assert ld.runtime_label({"server": "ollama", "version": "0.12.3"}) == (
            "ollama 0.12.3"
        )
        assert ld.runtime_label(None) == "not recorded"

    def test_serve_flags_drop_the_subcommand_and_the_host(self):
        # A port move is not a config change (provenance._serve_flags).
        assert ld.serve_flags(GENIEX_CPU) == "--compute cpu --nctx 16384 --log none"

    def test_an_unseen_lane_process_has_no_flags(self):
        assert ld.serve_flags({"server": "geniex", "serve_args": None}) == "-"

    def test_runtime_rows_carry_lane_endpoint_and_how_it_was_seen(self):
        config = speed_config(
            [],
            backend="geniex-cpu",
            model="unsloth/Qwen3-4B-GGUF:Q4_0",
            base_url="http://127.0.0.1:18184",
            runtime=GENIEX_CPU,
        )
        out = ld.runtime_rows([config])[0]
        assert out["lane"] == "geniex-cpu"
        assert out["model"] == "unsloth/Qwen3-4B-GGUF:Q4_0"
        assert out["endpoint"] == "127.0.0.1:18184"
        assert (out["seen"], out["source"]) == ("lane process", GENIEX_CPU["source"])
        assert out["kind"] == "throughput"

    def test_an_installed_binary_is_not_the_lane_process(self):
        # The coding grader runs in WSL2 and cannot see a Windows lane.
        config = speed_config([], runtime={**GENIEX_CPU, "verified": False})
        assert ld.runtime_rows([config])[0]["seen"] == "installed binary"

    def test_an_envelope_takes_lane_and_model_from_its_reports(self):
        config = {
            "label": "lanes",
            "kind": "bench_lanes",
            "unscored": [
                {"label": "geniex-npu", "model": "a"},
                {"label": "geniex-cpu", "model": "b"},
            ],
        }
        out = ld.runtime_rows([config])[0]
        assert (out["lane"], out["model"], out["kind"]) == (
            "geniex-npu, geniex-cpu",
            "a, b",
            "lanes",
        )
        assert (out["runtime"], out["seen"], out["endpoint"]) == (
            "not recorded",
            "-",
            "-",
        )


def contract(label, lane, answers, runtime=None):
    checks = [
        {"id": check_id, "question": f"{check_id}?", "answer": answer, "evidence": "e"}
        for check_id, answer in answers
    ]
    return {
        "label": label,
        "kind": "bench_contract",
        "runtime": runtime,
        "unscored": [{"label": lane, "model": "m", "checks": checks}],
    }


class TestContractTable:
    def test_columns_group_by_lane_and_rows_follow_the_checks(self):
        configs = [
            contract("v061-npu", "geniex-npu", [("a", "yes")]),
            contract("v061-cpu", "geniex-cpu", [("a", "no")]),
        ]
        table = ld.contract_table(configs)
        assert [c["file"] for c in table["columns"]] == ["v061-cpu", "v061-npu"]
        assert [[c["text"] for c in r] for r in table["rows"]] == [["a", "no", "yes"]]
        assert table["rows"][0][0] == {
            "text": "a",
            "tip": "a?",
            "state": "check",
            "moved": "",
        }

    def test_the_header_names_lane_and_version(self):
        runtime = {**GENIEX_CPU, "cli": "v0.6.1"}
        table = ld.contract_table(
            [contract("f", "geniex-cpu", [("a", "yes")], runtime)]
        )
        column = table["columns"][0]
        assert column["title"] == "geniex-cpu v0.6.1"
        assert "--compute cpu" in column["tip"]

    def test_a_check_added_later_sits_where_its_run_put_it(self):
        # The r2 contract added its determinism checks mid-list.
        configs = [
            contract("v061", "geniex-cpu", [("t0", "no"), ("seed", "no")]),
            contract(
                "v070r2",
                "geniex-cpu",
                [("t0", "no"), ("greedy", "yes"), ("seed", "yes")],
            ),
        ]
        rows = ld.contract_table(configs)["rows"]
        assert [r[0]["text"] for r in rows] == ["t0", "greedy", "seed"]
        assert [c["text"] for c in rows[1][1:]] == ["-", "yes"]

    def test_moved_marks_an_answer_that_changed_within_one_lane(self):
        configs = [
            contract("v061-cpu", "geniex-cpu", [("power", "no")]),
            contract("v061-npu", "geniex-npu", [("power", "no")]),
            contract("v070-cpu", "geniex-cpu", [("power", "yes")]),
            contract("v070-npu", "geniex-npu", [("power", "no")]),
        ]
        cells = ld.contract_table(configs)["rows"][0][1:]
        assert [(c["text"], c["moved"]) for c in cells] == [
            ("no", ""),
            ("yes", "yes"),  # geniex-cpu v061 -> v070
            ("no", ""),  # another lane answering differently is not a move
            ("no", ""),
        ]

    def test_a_check_one_run_never_asked_moves_nothing(self):
        configs = [
            contract("r1", "geniex-cpu", [("a", "yes"), ("b", "no")]),
            contract("r2", "geniex-cpu", [("a", "yes")]),
            contract("r3", "geniex-cpu", [("a", "yes"), ("b", "no")]),
        ]
        row_b = ld.contract_table(configs)["rows"][1]
        assert [(c["text"], c["moved"]) for c in row_b[1:]] == [
            ("no", ""),
            ("-", ""),
            ("no", ""),  # compared with r1's answer, not with r2's gap
        ]

    def test_errors_keep_their_state_and_evidence_carries_the_seconds(self):
        config = contract("f", "npu", [("completions_stop", "error")])
        config["unscored"][0]["checks"][0].update(evidence="HTTP 500", seconds=0.4)
        cell = ld.contract_table([config])["rows"][0][1]
        assert (cell["state"], cell["tip"]) == ("error", "HTTP 500 (0.4 s)")

    def test_other_kinds_are_not_contract_columns(self):
        speed = speed_config(
            [row()], unscored=[{"label": "x", "checks": [{"id": "a"}]}]
        )
        assert ld.contract_table([speed]) == {"columns": [], "rows": []}
