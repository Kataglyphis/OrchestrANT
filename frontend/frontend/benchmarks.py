"""The benchmark dashboard as a Reflex page.

Renders exactly the shapes ``benchmark_data`` and ``lab_data`` return, so the
arithmetic lives in tested plain functions and this module only lays out; the
lab's per-run cards are ``lab_cards``, the shared table pieces ``widgets``.

    cd frontend
    reflex run
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import reflex as rx

from frontend import benchmark_data, lab_data
from frontend.lab_cards import contract_card, lab_card, runtime_card
from frontend.widgets import CARD, card, cell, table

DEFAULT_MANIFEST = "benchmarks/benchmark_results/_manifest.json"
MANIFEST_ENV = "ORCHESTRANT_BENCHMARK_MANIFEST"
# frontend/frontend/benchmarks.py -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def manifest_path() -> Path:
    value = os.environ.get(MANIFEST_ENV, DEFAULT_MANIFEST)
    return benchmark_data.manifest_location(value, Path.cwd(), REPO_ROOT)


class ViewerState(rx.State):
    """Loads the manifest the runner wrote and derives every table from it."""

    manifest: dict[str, Any] = {}
    error: str = ""
    selected: int = -1

    @rx.event
    def load_manifest(self) -> None:
        path = manifest_path()
        try:
            self.manifest = json.loads(path.read_text(encoding="utf-8"))
            self.error = ""
        except (
            OSError,
            ValueError,
        ) as exc:  # a missing manifest is a state, not a crash
            self.error = f"{path}: {exc}"

    @rx.event
    def select(self, index: int) -> None:
        self.selected = -1 if self.selected == index else index

    @rx.var
    def configs(self) -> list[dict[str, Any]]:
        return self.manifest.get("configs", [])

    @rx.var
    def title(self) -> str:
        return str(self.manifest.get("title") or "Model benchmarks")

    @rx.var
    def model(self) -> str:
        return str(self.manifest.get("model") or "")

    @rx.var
    def generated(self) -> str:
        return str(self.manifest.get("generated") or "")

    # A typed flag rather than `ViewerState.manifest.length()` in the page: a
    # type checker reads the class attribute as the dict it is declared as.
    @rx.var
    def loaded(self) -> bool:
        return bool(self.manifest)

    @rx.var
    def hardware(self) -> list[dict[str, str]]:
        return benchmark_data.hardware_rows(self.manifest.get("host_hardware"))

    @rx.var
    def hardware_missing(self) -> list[str]:
        return benchmark_data.missing_hardware(self.manifest.get("host_hardware"))

    @rx.var
    def correctness(self) -> dict[str, Any]:
        return benchmark_data.correctness_summary(self.configs)

    # A var of its own because rx.foreach refuses one typed Any, which is what
    # correctness["rows"] is: the banner raised ForeachVarError at page compile.
    @rx.var
    def correctness_rows(self) -> list[dict[str, Any]]:
        return benchmark_data.correctness_summary(self.configs)["rows"]

    @rx.var
    def summary(self) -> dict[str, Any]:
        return benchmark_data.summary_stats(self.configs)

    @rx.var
    def scored(self) -> list[dict[str, Any]]:
        return benchmark_data.scored_rows(self.configs)

    @rx.var
    def has_scored(self) -> bool:
        return benchmark_data.scored_table_exists(self.configs)

    @rx.var
    def comparison(self) -> list[dict[str, Any]]:
        return benchmark_data.comparison_rows(self.configs)

    @rx.var
    def lab(self) -> list[dict[str, Any]]:
        return lab_data.lab_rows(self.configs)

    @rx.var
    def runtimes(self) -> list[dict[str, str]]:
        return lab_data.runtime_rows(self.configs)

    @rx.var
    def contract_columns(self) -> list[dict[str, str]]:
        return lab_data.contract_table(self.configs)["columns"]

    # Typed down to the cell: the grid is a foreach inside a foreach.
    @rx.var
    def contract_rows(self) -> list[list[dict[str, str]]]:
        return lab_data.contract_table(self.configs)["rows"]

    @rx.var
    def charts(self) -> list[dict[str, Any]]:
        specs = [
            ("Time to Finished Answer", "wall_s_to_answer", "Answer", "s", 1),
            ("Time to First Token", "ttft_s", "TTFT", "s", 2),
            ("Decode tok/s (no prefill)", "decode_tok_per_sec", "Decode", "tok/s", 1),
            ("Overall tok/s (whole request)", "tokens_per_sec", "Overall", "tok/s", 1),
            ("CPU Usage", "cpu_percent", "CPU", "%", 1),
            ("RAM Usage", "ram_used_gb", "RAM", "GB", 2),
            ("GPU Utilization", "gpu_utilization_percent", "GPU", "%", 1),
        ]
        return [
            {
                "title": title,
                "unit": unit,
                "data_key": key,
                "label": label,
                "data": benchmark_data.chart_series(self.configs, key, digits),
            }
            for title, key, label, unit, digits in specs
        ]

    @rx.var
    def selected_config(self) -> dict[str, Any]:
        if 0 <= self.selected < len(self.configs):
            return self.configs[self.selected]
        return {}

    @rx.var
    def detail(self) -> list[dict[str, str]]:
        return (
            benchmark_data.detail_rows(self.selected_config)
            if self.selected_config
            else []
        )

    @rx.var
    def prompts(self) -> list[dict[str, Any]]:
        return (
            benchmark_data.per_prompt_rows(self.selected_config)
            if self.selected_config
            else []
        )

    @rx.var
    def prompt_errors(self) -> list[dict[str, Any]]:
        return (
            benchmark_data.prompt_errors(self.selected_config)
            if self.selected_config
            else []
        )


def hardware_card() -> rx.Component:
    return rx.cond(
        ViewerState.hardware.length() > 0,
        card(
            rx.heading("Hardware", size="4"),
            table(
                [("Field", None), ("Value", None)],
                rx.foreach(
                    ViewerState.hardware,
                    lambda row: rx.el.tr(
                        cell(row["label"]),
                        cell(row["value"]),
                    ),
                ),
            ),
            rx.cond(
                ViewerState.hardware_missing.length() > 0,
                rx.text(
                    "Incomplete host record — missing: ",
                    ViewerState.hardware_missing.join(", "),
                    ". Cross-host comparisons with this run are unreliable.",
                    color="var(--orange-11)",
                    font_size="12px",
                ),
            ),
        ),
    )


def model_card() -> rx.Component:
    def stat(value: rx.Var, label: str) -> rx.Component:
        return rx.vstack(
            rx.text(value, font_size="20px", weight="bold"),
            rx.text(label, font_size="11px", color="var(--gray-11)"),
            spacing="0",
            align="center",
        )

    return card(
        rx.heading(ViewerState.title, size="4"),
        rx.hstack(
            stat(ViewerState.model, "Model"),
            stat(ViewerState.summary["configs"], "Configs"),
            stat(ViewerState.summary["requests"], "Requests"),
            stat(ViewerState.summary["errors"], "Errors"),
            stat(ViewerState.summary["avg_tps"], "Avg overall tok/s"),
            stat(ViewerState.generated, "Generated"),
            spacing="6",
            wrap="wrap",
        ),
    )


def correctness_banner() -> rx.Component:
    return rx.cond(
        ViewerState.correctness["checked"],
        card(
            rx.heading(
                "Correctness: ",
                ViewerState.correctness["headline"],
                " — ",
                ViewerState.correctness["score"].to_string(),
                "/",
                ViewerState.correctness["total"].to_string(),
                " integrity answers",
                size="4",
            ),
            rx.cond(
                ViewerState.correctness["capability"] != "",
                rx.text(ViewerState.correctness["capability"], font_size="12px"),
            ),
            rx.cond(
                ViewerState.correctness["state"] != "ok",
                rx.text(
                    "A wrong integrity answer means broken kernels or an over-aggressive "
                    "quantisation, not a slow model. Check the GGUF tensor types "
                    "(inspect_gguf.py) before tuning for speed — the speed numbers below "
                    "are meaningless if the output is wrong.",
                    font_size="12px",
                    color="var(--orange-11)",
                ),
            ),
            table(
                [
                    ("Config", None),
                    ("Score", None),
                    ("Kind", None),
                    ("Expected", None),
                    ("Answer", None),
                ],
                rx.foreach(
                    ViewerState.correctness_rows,
                    lambda row: rx.el.tr(
                        cell(rx.code(row["config"])),
                        cell(
                            rx.text(
                                # rx.cond: a Python `if` on a Var raises at compile.
                                rx.cond(row["ok"], "ok", "FAIL"),
                                color=rx.cond(
                                    row["ok"], "var(--green-11)", "var(--red-11)"
                                ),
                            )
                        ),
                        cell(row["kind"]),
                        cell(rx.code(row["expected"])),
                        cell(row["answer"], title=row["answer"]),
                    ),
                ),
            ),
        ),
        card(
            rx.heading("Correctness: not checked", size="4"),
            rx.text(
                "These runs measured speed only. A model emitting garbage scores excellent "
                "tokens/sec, so speed alone cannot tell a working model from a broken one. "
                "Re-run with --correctness to verify.",
                font_size="12px",
            ),
        ),
    )


def scored_card() -> rx.Component:
    return rx.cond(
        ViewerState.has_scored,
        card(
            rx.heading("Scored runs — coding and tool calling", size="4"),
            rx.text(
                "Scores carry their 95% interval. Two rows whose intervals overlap are "
                "NOT separable at this sample size, however different the fractions look.",
                font_size="12px",
                color="var(--gray-11)",
            ),
            table(
                [
                    ("Benchmark", None),
                    ("Model", None),
                    ("Score [95% CI]", None),
                    ("cut", "Cut off by a server output cap — unmeasured, not failed"),
                    ("err", "Transport failures, excluded from the denominator"),
                    ("Total (s)", None),
                    ("det.", "Repeats on a deterministic endpoint add no information"),
                ],
                rx.foreach(
                    ViewerState.scored,
                    lambda row: rx.el.tr(
                        cell(rx.code(row["kind"])),
                        cell(row["label"], title=row["label"]),
                        cell(
                            rx.text(
                                row["passed"].to_string(),
                                "/",
                                row["total"].to_string(),
                                " = ",
                                row["pct"].to_string(),
                                "% [",
                                row["low"].to_string(),
                                "–",
                                row["high"].to_string(),
                                "%]",
                            )
                        ),
                        cell(row["truncated"]),
                        cell(row["errored"]),
                        cell(row["wall"]),
                        cell(
                            rx.cond(row["deterministic"], rx.text("yes"), rx.text(""))
                        ),
                    ),
                ),
            ),
        ),
    )


def comparison_card() -> rx.Component:
    return card(
        rx.heading("Config Comparison", size="4"),
        table(
            [
                ("Config", None),
                ("num_ctx", None),
                ("max_tokens", None),
                (
                    "Answer (s)",
                    "Wall time to a finished answer — rank by this, not by T/s",
                ),
                (
                    "TTFT (s)",
                    "Time to first token: what a user waits on before anything appears",
                ),
                (
                    "Decode tok/s",
                    (
                        "Tokens after the first over the seconds spent decoding "
                        "them, all requests pooled — the runner's Decode line"
                    ),
                ),
                (
                    "Overall tok/s",
                    (
                        "Completion tokens over the whole wall time, prefill and "
                        "thinking included, all requests pooled — the runner's "
                        "Overall line"
                    ),
                ),
                (
                    "Think",
                    "Share of the output spent inside a think block — pure latency for an agent",
                ),
                ("CPU %", None),
                ("RAM (GB)", None),
                (
                    "GPU %",
                    "Local GPU utilization during the request; blank when no GPU was readable",
                ),
                ("Comp. Tokens", None),
                ("Prompt Tokens", None),
                ("OK", None),
            ],
            rx.foreach(
                ViewerState.comparison,
                lambda row: rx.el.tr(
                    cell(rx.code(row["label"])),
                    cell(row["ctx"]),
                    cell(row["tok"]),
                    cell(row["answer"]),
                    cell(row["ttft"]),
                    cell(row["decode"]),
                    cell(row["tps"]),
                    cell(row["think"]),
                    cell(row["cpu"]),
                    cell(row["ram"]),
                    cell(row["gpu"]),
                    cell(row["completion"]),
                    cell(row["prompt"]),
                    cell(row["ok"]),
                ),
            ),
        ),
    )


def chart_block(block: rx.Var) -> rx.Component:
    return card(
        rx.heading(block["title"], size="4"),
        rx.cond(
            # .to(list): a dict[str, Any] item is untyped, and .length() on it
            # raised UntypedVarError at page compile.
            block["data"].to(list).length() == 0,
            rx.text(
                "No run recorded ",
                rx.code(block["data_key"]),
                ". Streaming metrics need --stream; older result files predate them.",
                font_size="12px",
                color="var(--orange-11)",
            ),
            rx.recharts.bar_chart(
                rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                rx.recharts.bar(
                    data_key="value", name=block["label"], fill="var(--violet-9)"
                ),
                rx.recharts.x_axis(
                    data_key="name", angle=-25, text_anchor="end", interval=0
                ),
                rx.recharts.y_axis(),
                rx.recharts.tooltip(),
                data=block["data"],
                height=300,
                width="100%",
            ),
        ),
    )


_PROMPT_HEADERS: list[tuple[str, str | None]] = [
    ("#", None),
    ("Prompt", None),
    ("PT", None),
    ("CT", None),
    ("Answer (s)", "Wall time to a finished answer; cut = stopped at max_tokens"),
    ("First answer (s)", "First answer token, after any thinking"),
    ("TTFT (s)", "Time to first token"),
    ("Decode", "Decode rate excluding prefill"),
    ("Prefill", "Prompt tokens processed per second before the first token"),
    ("Think", "Share of output inside a think block"),
    ("T/s", None),
    ("CPU%", None),
    ("Lane cores", "Cores the serving process tree used during this request"),
    ("Other cores", "Everything else the machine ran; * derived for an older report"),
    ("J/tok", "CPU-rail joules per completion token, gross"),
    ("J/tok net", "Net of the idle baseline"),
    ("RAM (GB)", None),
    ("GPU%", None),
    ("Busiest proc", "Process that burned the most CPU during this request"),
]


def _prompt_row(row: rx.Var) -> rx.Component:
    return rx.el.tr(
        cell(row["index"]),
        cell(row["prompt"], title=row["prompt"]),
        cell(row["pt"]),
        cell(
            rx.text(
                row["ct"].to_string(),
                rx.cond(row["estimated"], rx.text("*"), rx.text("")),
            )
        ),
        cell(row["answer"]),
        cell(row["ttfa"]),
        cell(row["ttft"]),
        cell(row["decode"]),
        cell(row["prefill"]),
        cell(row["think"]),
        cell(row["tps"]),
        cell(row["cpu"]),
        cell(row["lane"]),
        cell(row["other"]),
        cell(row["jtok"]),
        cell(row["jnet"]),
        cell(row["ram"]),
        cell(row["gpu"]),
        cell(row["busiest"]),
    )


def prompt_table() -> rx.Component:
    """The selected run's per-prompt rows; a wide table scrolls in its card."""
    return rx.cond(
        ViewerState.prompts.length() > 0,
        rx.vstack(
            rx.heading("Per-Prompt Results", size="3"),
            rx.box(
                table(_PROMPT_HEADERS, rx.foreach(ViewerState.prompts, _prompt_row)),
                overflow_x="auto",
                width="100%",
            ),
            width="100%",
        ),
    )


def drill_down() -> rx.Component:
    return card(
        rx.heading("Drill Down", size="4"),
        rx.hstack(
            rx.foreach(
                ViewerState.configs,
                lambda config, index: rx.button(
                    config["label"],
                    on_click=ViewerState.select(index),
                    variant=rx.cond(ViewerState.selected == index, "solid", "soft"),
                    size="2",
                ),
            ),
            wrap="wrap",
            spacing="2",
        ),
        rx.cond(
            ViewerState.selected_config,
            rx.vstack(
                table(
                    [("Field", None), ("Value", None)],
                    rx.foreach(
                        ViewerState.detail,
                        lambda row: rx.el.tr(cell(row["label"]), cell(row["value"])),
                    ),
                ),
                prompt_table(),
                rx.cond(
                    ViewerState.prompt_errors.length() > 0,
                    rx.vstack(
                        rx.heading("Errors", size="3"),
                        rx.foreach(
                            ViewerState.prompt_errors,
                            lambda err: rx.text(
                                "#", err["index"].to_string(), ": ", err["error"]
                            ),
                        ),
                    ),
                ),
                spacing="4",
                width="100%",
            ),
        ),
        width="100%",
    )


def error_panel() -> rx.Component:
    return rx.box(
        rx.heading("Failed to load benchmark data", size="5"),
        rx.text(ViewerState.error, font_size="12px"),
        rx.text(
            f"Set {MANIFEST_ENV} or run the benchmarks first so the manifest exists.",
            font_size="12px",
        ),
        **CARD,
    )


@rx.page(route="/", title="LLM Benchmark Viewer", on_load=ViewerState.load_manifest)
def benchmarks() -> rx.Component:
    return rx.container(
        rx.color_mode.button(position="top-right", top="1rem", right="1rem"),
        rx.vstack(
            rx.cond(ViewerState.error != "", error_panel()),
            rx.cond(
                (ViewerState.error == "") & ~ViewerState.loaded,
                rx.text("Loading benchmark data…"),
            ),
            rx.cond(
                (ViewerState.error == "") & ViewerState.loaded,
                rx.vstack(
                    rx.heading("LLM Benchmark Viewer", size="7"),
                    rx.text(ViewerState.title, color="var(--gray-11)"),
                    rx.hstack(
                        model_card(),
                        hardware_card(),
                        width="100%",
                        spacing="4",
                        wrap="wrap",
                    ),
                    correctness_banner(),
                    scored_card(),
                    comparison_card(),
                    lab_card(ViewerState.lab),
                    runtime_card(ViewerState.runtimes),
                    contract_card(
                        ViewerState.contract_columns, ViewerState.contract_rows
                    ),
                    rx.grid(
                        rx.foreach(ViewerState.charts, chart_block),
                        columns="2",
                        spacing="4",
                        width="100%",
                    ),
                    drill_down(),
                    rx.text(
                        "Generated ",
                        ViewerState.generated,
                        " · LLM Stack Benchmark Suite",
                        font_size="11px",
                        color="var(--gray-11)",
                    ),
                    spacing="4",
                    width="100%",
                ),
            ),
            spacing="4",
            width="100%",
            padding="1.5rem",
        ),
        size="4",
    )
