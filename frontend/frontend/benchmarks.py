"""The benchmark dashboard as a Flet page, laying out what the data modules return."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from . import benchmark_data, lab_data
from .lab_cards import contract_card, lab_card, runtime_card
from .widgets import (
    BAD,
    GOOD,
    MUTED,
    WARN,
    card,
    clipped,
    heading,
    note,
    shown,
    table,
    text,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from .widgets import Headers

DEFAULT_MANIFEST = "benchmarks/benchmark_results/_manifest.json"
MANIFEST_ENV = "ORCHESTRANT_BENCHMARK_MANIFEST"
# frontend/frontend/benchmarks.py -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
TITLE = "LLM Benchmark Viewer"
# ANThology's default seed, so the viewer reads as one of the family's Flutter apps.
SEED = ft.Colors.GREEN_ACCENT

# (title, metric, unit, digits) per bar chart.
CHARTS = [
    ("Time to Finished Answer", "wall_s_to_answer", "s", 1),
    ("Time to First Token", "ttft_s", "s", 2),
    ("Decode tok/s (no prefill)", "decode_tok_per_sec", "tok/s", 1),
    ("Overall tok/s (whole request)", "tokens_per_sec", "tok/s", 1),
    ("CPU Usage", "cpu_percent", "%", 1),
    ("RAM Usage", "ram_used_gb", "GB", 2),
    ("GPU Utilization", "gpu_utilization_percent", "%", 1),
]


def manifest_path() -> Path:
    """$ORCHESTRANT_BENCHMARK_MANIFEST, else the default, read from the repository root."""
    value = os.environ.get(MANIFEST_ENV, DEFAULT_MANIFEST)
    return benchmark_data.manifest_location(value, Path.cwd(), REPO_ROOT)


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    """The manifest and no error, or nothing and why: a missing manifest is a state, not a crash."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"{path}: {exc}"
    if not isinstance(manifest, dict):
        return {}, f"{path}: not a manifest (a JSON object)"
    return manifest, ""


def hardware_card(hw: dict[str, Any] | None) -> ft.Control | None:
    """The host record, and a warning when it is incomplete; nothing without one."""
    rows = benchmark_data.hardware_rows(hw)
    if not rows:
        return None
    missing = benchmark_data.missing_hardware(hw)
    return card(
        heading("Hardware"),
        table(
            [("Field", None), ("Value", None)], [[r["label"], r["value"]] for r in rows]
        ),
        note(
            f"Incomplete host record — missing: {', '.join(missing)}. "
            "Cross-host comparisons with this run are unreliable.",
            color=WARN,
        )
        if missing
        else None,
    )


def _stat(value: Any, label: str) -> ft.Column:
    return ft.Column(
        [text(value, size=20, bold=True), text(label, size=11, color=MUTED)],
        spacing=0,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )


def model_card(manifest: dict[str, Any], configs: list[dict[str, Any]]) -> ft.Control:
    """The run's title and its six headline numbers."""
    summary = benchmark_data.summary_stats(configs)
    stats = [
        (manifest.get("model") or "", "Model"),
        (summary["configs"], "Configs"),
        (summary["requests"], "Requests"),
        (summary["errors"], "Errors"),
        (summary["avg_tps"], "Avg overall tok/s"),
        (manifest.get("generated") or "", "Generated"),
    ]
    return card(
        heading(str(manifest.get("title") or "Model benchmarks")),
        ft.Row([_stat(v, label) for v, label in stats], wrap=True, spacing=28),
    )


_CORRECTNESS_HEADERS: Headers = [
    ("Config", None),
    ("Score", None),
    ("Kind", None),
    ("Expected", None),
    ("Answer", None),
]


def correctness_card(configs: list[dict[str, Any]]) -> ft.Control:
    """Is the model working, not just fast: the integrity answers, or that none were asked."""
    summary = benchmark_data.correctness_summary(configs)
    if not summary["checked"]:
        return card(
            heading("Correctness: not checked"),
            note(
                "These runs measured speed only. A model emitting garbage scores "
                "excellent tokens/sec, so speed alone cannot tell a working model from "
                "a broken one. Re-run with --correctness to verify.",
                color=None,
            ),
        )
    rows = [
        [
            text(row["config"], mono=True),
            text("ok" if row["ok"] else "FAIL", color=GOOD if row["ok"] else BAD),
            row["kind"],
            text(row["expected"], mono=True),
            clipped(row["answer"]),
        ]
        for row in summary["rows"]
    ]
    return card(
        heading(
            f"Correctness: {summary['headline']} — "
            f"{summary['score']}/{summary['total']} integrity answers"
        ),
        note(summary["capability"], color=None) if summary["capability"] else None,
        note(
            "A wrong integrity answer means broken kernels or an over-aggressive "
            "quantisation, not a slow model. Check the GGUF tensor types "
            "(inspect_gguf.py) before tuning for speed — the speed numbers below "
            "are meaningless if the output is wrong.",
            color=WARN,
        )
        if summary["state"] != "ok"
        else None,
        table(_CORRECTNESS_HEADERS, rows),
    )


_SCORED_HEADERS: Headers = [
    ("Benchmark", None),
    ("Model", None),
    ("Score [95% CI]", None),
    ("cut", "Cut off by a server output cap — unmeasured, not failed"),
    ("err", "Transport failures, excluded from the denominator"),
    ("Total (s)", None),
    ("det.", "Repeats on a deterministic endpoint add no information"),
]


def scored_card(configs: list[dict[str, Any]]) -> ft.Control | None:
    """Coding and tool-calling scores with their 95% intervals; nothing without such runs."""
    if not benchmark_data.scored_table_exists(configs):
        return None
    rows = [
        [
            text(row["kind"], mono=True),
            clipped(row["label"]),
            (
                f"{shown(row['passed'])}/{shown(row['total'])} = {row['pct']}% "
                f"[{row['low']}–{row['high']}%]"
            ),
            row["truncated"],
            row["errored"],
            row["wall"],
            "yes" if row["deterministic"] else "",
        ]
        for row in benchmark_data.scored_rows(configs)
    ]
    return card(
        heading("Scored runs — coding and tool calling"),
        note(
            "Scores carry their 95% interval. Two rows whose intervals overlap are "
            "NOT separable at this sample size, however different the fractions look."
        ),
        table(_SCORED_HEADERS, rows),
    )


_COMPARISON_HEADERS: Headers = [
    ("Config", None),
    ("num_ctx", None),
    ("max_tokens", None),
    ("Answer (s)", "Wall time to a finished answer — rank by this, not by T/s"),
    ("TTFT (s)", "Time to first token: what a user waits on before anything appears"),
    (
        "Decode tok/s",
        (
            "Tokens after the first over the seconds spent decoding them, all "
            "requests pooled — the runner's Decode line"
        ),
    ),
    (
        "Overall tok/s",
        (
            "Completion tokens over the whole wall time, prefill and thinking "
            "included, all requests pooled — the runner's Overall line"
        ),
    ),
    (
        "Think",
        (
            "Share of the output spent inside a think block — pure latency for an "
            "agent; N unknown = replies cut before any think marker, left out of "
            "the mean"
        ),
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
]

_COMPARISON_KEYS = (
    "ctx", "tok", "answer", "ttft", "decode", "tps", "think",
    "cpu", "ram", "gpu", "completion", "prompt", "ok",
)  # fmt: skip


def comparison_card(configs: list[dict[str, Any]]) -> ft.Control:
    """One row per config, its speed figures as the manifest records them."""
    rows = [
        [text(row["label"], mono=True), *(row[key] for key in _COMPARISON_KEYS)]
        for row in benchmark_data.comparison_rows(configs)
    ]
    return card(heading("Config Comparison"), table(_COMPARISON_HEADERS, rows))


def _bar(name: str, value: float, top: float, unit: str) -> ft.Row:
    return ft.Row(
        [
            clipped(name, width=220),
            ft.ProgressBar(
                value=max(0.0, value / top),
                expand=True,
                bar_height=14,
                border_radius=ft.BorderRadius.all(4),
            ),
            ft.Container(content=text(f"{value} {unit}"), width=100),
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def chart_card(
    configs: list[dict[str, Any]], spec: tuple[str, str, str, int]
) -> ft.Control:
    """One metric as bars scaled to its largest value, each with its value printed."""
    title, key, unit, digits = spec
    series = benchmark_data.chart_series(configs, key, digits)
    if not series:
        return card(
            heading(title, size=16),
            note(
                f"No run recorded {key}. Streaming metrics need --stream; older "
                "result files predate them.",
                color=WARN,
            ),
        )
    top = max(point["value"] for point in series) or 1
    return card(
        heading(title, size=16),
        *(_bar(point["name"], point["value"], top, unit) for point in series),
    )


_PROMPT_HEADERS: Headers = [
    ("#", None),
    ("Prompt", None),
    ("PT", None),
    ("CT", None),
    ("Answer (s)", "Wall time to a finished answer; cut = stopped at max_tokens"),
    ("First answer (s)", "First answer token, after any thinking"),
    ("TTFT (s)", "Time to first token"),
    ("Decode", "Decode rate excluding prefill"),
    ("Prefill", "Prompt tokens processed per second before the first token"),
    ("Think", "Share of output inside a think block; ? = cut before any marker"),
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

_PROMPT_KEYS = (
    "answer", "ttfa", "ttft", "decode", "prefill", "think", "tps",
    "cpu", "lane", "other", "jtok", "jnet", "ram", "gpu", "busiest",
)  # fmt: skip


def _prompt_row(row: dict[str, Any]) -> list[Any]:
    estimated = "*" if row["estimated"] else ""
    return [
        row["index"],
        clipped(row["prompt"]),
        row["pt"],
        f"{shown(row['ct'])}{estimated}",
        *(row[key] for key in _PROMPT_KEYS),
    ]


def selected_detail(config: dict[str, Any]) -> list[ft.Control]:
    """A run's settings, its per-prompt rows and its failed requests."""
    details = benchmark_data.detail_rows(config)
    parts: list[ft.Control] = [
        table(
            [("Field", None), ("Value", None)],
            [[r["label"], r["value"]] for r in details],
        )
    ]
    prompts = benchmark_data.per_prompt_rows(config)
    if prompts:
        parts += [
            heading("Per-Prompt Results", size=15),
            table(_PROMPT_HEADERS, [_prompt_row(row) for row in prompts]),
        ]
    errors = benchmark_data.prompt_errors(config)
    if errors:
        parts += [
            heading("Errors", size=15),
            *(text(f"#{shown(e['index'])}: {e['error']}") for e in errors),
        ]
    return parts


def toggled(selected: int, index: int) -> int:
    """Clicking the open run closes it; any other run opens."""
    return -1 if selected == index else index


class Viewer:
    """One loaded manifest and the drill-down's open run; `controls()` is the page body."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        """Lay the drill-down out with no run open."""
        self.manifest = manifest
        self.configs: list[dict[str, Any]] = manifest.get("configs") or []
        self.selected = -1
        self.on_change: Callable[[], None] | None = None
        self.drill = ft.Container(content=self._drill_down())

    def select(self, index: int) -> None:
        """Open run `index`, or close it when it is the open one, and redraw."""
        self.selected = toggled(self.selected, index)
        self.drill.content = self._drill_down()
        if self.on_change is not None:
            self.on_change()

    def _button(self, index: int, config: dict[str, Any]) -> ft.Control:
        kind = ft.FilledButton if index == self.selected else ft.OutlinedButton
        return kind(
            content=str(config.get("label", "")),
            on_click=lambda _event: self.select(index),
        )

    def _drill_down(self) -> ft.Control:
        buttons = [self._button(i, config) for i, config in enumerate(self.configs)]
        parts: list[ft.Control] = [
            heading("Drill Down"),
            ft.Row(buttons, wrap=True, spacing=8, run_spacing=8),
        ]
        if 0 <= self.selected < len(self.configs):
            parts += selected_detail(self.configs[self.selected])
        return card(*parts)

    def controls(self) -> list[ft.Control]:
        """Every card in page order, the ones with nothing to show left out."""
        configs = self.configs
        contract = lab_data.contract_table(configs)
        halves = [
            model_card(self.manifest, configs),
            hardware_card(self.manifest.get("host_hardware")),
        ]
        parts = [
            ft.ResponsiveRow([_half(c) for c in halves if c is not None]),
            correctness_card(configs),
            scored_card(configs),
            comparison_card(configs),
            lab_card(lab_data.lab_rows(configs)),
            runtime_card(lab_data.runtime_rows(configs)),
            contract_card(contract["columns"], contract["rows"]),
            ft.ResponsiveRow([_half(chart_card(configs, spec)) for spec in CHARTS]),
            self.drill,
            note(
                f"Generated {shown(self.manifest.get('generated'))} · LLM Stack Benchmark Suite"
            ),
        ]
        return [part for part in parts if part is not None]


def _half(control: ft.Control) -> ft.Container:
    """Two per row on a wide window, one per row on a narrow one."""
    return ft.Container(content=control, col={"xs": 12, "lg": 6})


def error_view(error: str) -> list[ft.Control]:
    """What the page shows instead when the manifest cannot be read."""
    return [
        card(
            heading("Failed to load benchmark data", size=20),
            note(error, color=None),
            note(
                f"Set {MANIFEST_ENV} or run the benchmarks first so the manifest exists."
            ),
        )
    ]


def _header(page: ft.Page, subtitle: str) -> ft.Row:
    def flip(_event: ft.Event[ft.IconButton]) -> None:
        dark = page.theme_mode == ft.ThemeMode.DARK
        page.theme_mode = ft.ThemeMode.LIGHT if dark else ft.ThemeMode.DARK
        page.update()

    return ft.Row(
        [
            ft.Column(
                [heading(TITLE, size=28), text(subtitle, color=MUTED)],
                spacing=2,
                tight=True,
            ),
            ft.IconButton(
                icon=ft.Icons.BRIGHTNESS_6, tooltip="Light / dark", on_click=flip
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )


def main(page: ft.Page) -> None:
    """One session: read the manifest once, lay the page out, redraw the drill-down on clicks."""
    page.title = TITLE
    page.theme = ft.Theme(color_scheme_seed=SEED)
    page.dark_theme = ft.Theme(color_scheme_seed=SEED)
    page.scroll = ft.ScrollMode.AUTO
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.padding = 24
    manifest, error = load_manifest(manifest_path())
    if error:
        page.add(_header(page, ""), *error_view(error))
        return
    viewer = Viewer(manifest)
    viewer.on_change = page.update
    page.add(_header(page, str(manifest.get("title") or "")), *viewer.controls())
