"""The benchmark dashboard as a Reflex page.

Renders exactly the shapes ``orchestrant.frontend.benchmark_data`` returns, so
the arithmetic lives in tested plain functions and this module only lays out.

    cd frontend
    reflex run
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import reflex as rx

from frontend import benchmark_data

DEFAULT_MANIFEST = "benchmarks/benchmark_results/_manifest.json"
MANIFEST_ENV = "ORCHESTRANT_BENCHMARK_MANIFEST"

CARD = {
    "border": "1px solid var(--gray-a5)",
    "border_radius": "10px",
    "padding": "1rem 1.25rem",
    "width": "100%",
}


def manifest_path() -> Path:
    return Path(os.environ.get(MANIFEST_ENV, DEFAULT_MANIFEST))


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

    @rx.var
    def hardware(self) -> list[dict[str, str]]:
        return benchmark_data.hardware_rows(self.manifest.get("host_hardware"))

    @rx.var
    def hardware_missing(self) -> list[str]:
        return benchmark_data.missing_hardware(self.manifest.get("host_hardware"))

    @rx.var
    def correctness(self) -> dict[str, Any]:
        return benchmark_data.correctness_summary(self.configs)

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
    def charts(self) -> list[dict[str, Any]]:
        specs = [
            ("Time to Finished Answer", "wall_s_to_answer", "Answer", "s", 1),
            ("Time to First Token", "ttft_s", "TTFT", "s", 2),
            ("Decode Rate (excl. prefill)", "decode_tok_per_sec", "Decode", "tok/s", 1),
            ("Tokens per Second (overall)", "tokens_per_sec", "T/s", "tok/s", 1),
            ("CPU Usage", "cpu_percent", "CPU", "%", 1),
            ("RAM Usage", "ram_used_gb", "RAM", "GB", 2),
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


def _cell(text: Any, *, header: bool = False, title: str | None = None) -> rx.Component:
    return (
        rx.el.th(text, title=title, py="2", px="3", text_align="left")
        if header
        else rx.el.td(
            text, title=title, py="2", px="3", border_top="1px solid var(--gray-a4)"
        )
    )


def _table(headers: list[tuple[str, str | None]], rows: rx.Var) -> rx.Component:
    return rx.el.table(
        rx.el.thead(
            rx.el.tr(
                *[_cell(name, header=True, title=tip) for name, tip in headers],
                background="var(--gray-a3)",
            )
        ),
        rx.el.tbody(rows),
        width="100%",
        font_size="13px",
        border_collapse="collapse",
    )


def card(*children: rx.Component) -> rx.Component:
    return rx.box(*children, **CARD)


def hardware_card() -> rx.Component:
    return rx.cond(
        ViewerState.hardware.length() > 0,
        card(
            rx.heading("Hardware", size="4"),
            _table(
                [("Field", None), ("Value", None)],
                rx.foreach(
                    ViewerState.hardware,
                    lambda row: rx.el.tr(
                        _cell(row["label"]),
                        _cell(row["value"]),
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
            stat(ViewerState.summary["avg_tps"], "Avg T/s"),
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
                " verifiable answers",
                size="4",
            ),
            rx.cond(
                ViewerState.correctness["state"] != "ok",
                rx.text(
                    "Wrong answers here usually mean broken kernels or an over-aggressive "
                    "quantisation, not a slow model. Check the GGUF tensor types "
                    "(inspect_gguf.py) before tuning for speed — the speed numbers below "
                    "are meaningless if the output is wrong.",
                    font_size="12px",
                    color="var(--orange-11)",
                ),
            ),
            _table(
                [
                    ("Config", None),
                    ("Score", None),
                    ("Expected", None),
                    ("Answer", None),
                ],
                rx.foreach(
                    ViewerState.correctness["rows"],
                    lambda row: rx.el.tr(
                        _cell(rx.code(row["config"])),
                        _cell(
                            rx.text(
                                "ok" if row["ok"] else "FAIL",
                                color=rx.cond(
                                    row["ok"], "var(--green-11)", "var(--red-11)"
                                ),
                            )
                        ),
                        _cell(rx.code(row["expected"])),
                        _cell(row["answer"], title=row["answer"]),
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
            _table(
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
                        _cell(rx.code(row["kind"])),
                        _cell(row["label"], title=row["label"]),
                        _cell(
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
                        ),
                        _cell(row["truncated"]),
                        _cell(row["errored"]),
                        _cell(row["wall"]),
                        _cell(
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
        _table(
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
                ("Decode T/s", "Decode rate excluding prefill"),
                (
                    "T/s",
                    "Overall rate; divides by the whole request, so it mixes prefill in",
                ),
                (
                    "Think",
                    "Share of the output spent inside a think block — pure latency for an agent",
                ),
                ("CPU %", None),
                ("RAM (GB)", None),
                ("Comp. Tokens", None),
                ("Prompt Tokens", None),
                ("OK", None),
            ],
            rx.foreach(
                ViewerState.comparison,
                lambda row: rx.el.tr(
                    _cell(rx.code(row["label"])),
                    _cell(row["ctx"]),
                    _cell(row["tok"]),
                    _cell(row["answer"]),
                    _cell(row["ttft"]),
                    _cell(row["decode"]),
                    _cell(row["tps"]),
                    _cell(row["think"]),
                    _cell(row["cpu"]),
                    _cell(row["ram"]),
                    _cell(row["completion"]),
                    _cell(row["prompt"]),
                    _cell(row["ok"]),
                ),
            ),
        ),
    )


def chart_block(block: rx.Var) -> rx.Component:
    return card(
        rx.heading(block["title"], size="4"),
        rx.cond(
            block["data"].length() == 0,
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
                _table(
                    [("Field", None), ("Value", None)],
                    rx.foreach(
                        ViewerState.detail,
                        lambda row: rx.el.tr(_cell(row["label"]), _cell(row["value"])),
                    ),
                ),
                rx.cond(
                    ViewerState.prompts.length() > 0,
                    rx.vstack(
                        rx.heading("Per-Prompt Results", size="3"),
                        _table(
                            [
                                ("#", None),
                                ("Prompt", None),
                                ("PT", None),
                                ("CT", None),
                                ("Answer (s)", "Wall time to a finished answer"),
                                ("TTFT (s)", "Time to first token"),
                                ("Decode", "Decode rate excluding prefill"),
                                (
                                    "Prefill",
                                    "Prompt tokens processed per second before the first token",
                                ),
                                ("Think", "Share of output inside a think block"),
                                ("T/s", None),
                                ("CPU%", None),
                                ("RAM (GB)", None),
                                (
                                    "Busiest proc",
                                    "Process that burned the most CPU during this request",
                                ),
                            ],
                            rx.foreach(
                                ViewerState.prompts,
                                lambda row: rx.el.tr(
                                    _cell(row["index"]),
                                    _cell(row["prompt"], title=row["prompt"]),
                                    _cell(row["pt"]),
                                    _cell(
                                        rx.text(
                                            row["ct"].to_string(),
                                            rx.cond(
                                                row["estimated"],
                                                rx.text("*"),
                                                rx.text(""),
                                            ),
                                        )
                                    ),
                                    _cell(row["answer"]),
                                    _cell(row["ttft"]),
                                    _cell(row["decode"]),
                                    _cell(row["prefill"]),
                                    _cell(row["think"]),
                                    _cell(row["tps"]),
                                    _cell(row["cpu"]),
                                    _cell(row["ram"]),
                                    _cell(row["busiest"]),
                                ),
                            ),
                        ),
                    ),
                ),
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
                (ViewerState.error == "") & (ViewerState.manifest.length() == 0),
                rx.text("Loading benchmark data…"),
            ),
            rx.cond(
                (ViewerState.error == "") & (ViewerState.manifest.length() > 0),
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
