"""The cards for what the lab records since 2026-09-24.

Per run: whether answers arrived, the load and CPU-rail energy they cost, and
the server build that served them; across runs, the contract probe as a
check x run grid. Each card takes rows ``lab_data`` already shaped, so this
module only lays out -- and it never imports the page, which imports it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from .widgets import BAD, GOOD, MUTED, WARN, card, clipped, heading, note, table, text


if TYPE_CHECKING:
    from .widgets import Headers


_LAB_HEADERS: Headers = [
    ("Run", None),
    ("Answered", "Replies finished inside max_tokens"),
    (
        "First answer (s)",
        (
            "Mean time to the first answer token, after any thinking, over the "
            "answered replies"
        ),
    ),
    ("Think", "Share of the output spent thinking; N unknown = cut before any marker"),
    ("Lane cores", "Cores the serving process tree used"),
    (
        "Other cores",
        (
            "Everything else the machine ran, mean (max); * derived as "
            "cpu_percent x threads - lane cores for a report older than the field"
        ),
    ),
    ("J/tok", "CPU-rail joules per completion token, gross"),
    ("J/tok net", "Net of the idle baseline"),
    (
        "Net",
        (
            "Whether the idle baseline held between before and after the "
            "requests; drifted means read gross"
        ),
    ),
    ("W", "Mean CPU-rail power over the requests"),
]

_RUNTIME_HEADERS: Headers = [
    ("Run", None),
    ("Kind", None),
    ("Lane", None),
    ("Model", None),
    (
        "Endpoint",
        "Whose runtime is recorded: a lanes report names only its first lane's",
    ),
    ("Runtime", "provenance.runtime: server, CLI, QAIRT, llama.cpp"),
    ("Seen", "Where the build was read from"),
    ("Serve flags", "The lane's command line, minus --host"),
]

_NET_COLORS = {"reliable": GOOD, "drifted": WARN}


def lab_card(rows: list[dict[str, Any]]) -> ft.Control | None:
    """Answered k/n, first answer, thinking, lane and other load, joules."""
    if not rows:
        return None
    return card(
        heading("Answers, load and energy"),
        note(
            "A reply cut at max_tokens is not an answer, and its time to one was "
            "not measured. Energy is the CPU-cluster rails only -- an NPU lane's "
            "own draw has no rail -- per completion token, as a ratio of sums. "
            "A CPU lane's speed is conditional on the other load beside it."
        ),
        table(_LAB_HEADERS, [_lab_row(row) for row in rows]),
    )


def _lab_row(row: dict[str, Any]) -> list[Any]:
    return [
        text(row["label"], mono=True),
        text(row["answered"], color=WARN if row["cut"] else None),
        row["ttfa"],
        row["think"],
        row["lane_cores"],
        row["other_cores"],
        row["j_gross"],
        row["j_net"],
        text(
            row["net_text"],
            color=_NET_COLORS.get(row["net_state"], MUTED),
            tooltip=row["net_note"],
        ),
        row["watts"],
    ]


def runtime_card(rows: list[dict[str, str]]) -> ft.Control | None:
    """Which build served each run, and the flags its lane was launched with."""
    if not rows:
        return None
    return card(
        heading("Serving runtime"),
        note(
            "Two runs of one lane on different builds or flags are different "
            "measurements: on GenieX v0.7.0, --log info alone cost the NPU lane "
            "13 % of its decode rate. 'installed binary' means the lane process "
            "was not visible (a WSL2 client), so the build is the installed one, "
            "not proven to be the one serving."
        ),
        table(
            _RUNTIME_HEADERS,
            [
                [
                    text(row["label"], mono=True),
                    row["kind"],
                    row["lane"],
                    clipped(row["model"]),
                    row["endpoint"],
                    row["runtime"],
                    text(row["seen"], tooltip=row["source"]),
                    text(row["flags"], mono=True),
                ]
                for row in rows
            ],
        ),
    )


def _contract_cell(item: dict[str, str]) -> ft.Text:
    if item["state"] == "check":
        return text(item["text"], mono=True, tooltip=item["tip"])
    moved = item["moved"] == "yes"
    color = ft.Colors.PRIMARY if moved else BAD if item["state"] == "error" else None
    return text(item["text"], bold=moved, color=color, tooltip=item["tip"])


def _contract_header(column: dict[str, str]) -> tuple[ft.Control, str | None]:
    label = ft.Column(
        [text(column["title"], bold=True), text(column["file"], size=11, color=MUTED)],
        spacing=0,
        tight=True,
    )
    return label, column["tip"] or None


def contract_card(
    columns: list[dict[str, str]], rows: list[list[dict[str, str]]]
) -> ft.Control | None:
    """`orchestrant-bench contract` as one check x run grid, per lane / version."""
    if not columns:
        return None
    headers: Headers = [("Check", None), *(_contract_header(c) for c in columns)]
    return card(
        heading("Server contract"),
        note(
            "What each lane does, asked of the lane itself (orchestrant-bench "
            "contract). Highlighted: the answer moved since the lane's previous "
            "run -- what an upgrade changed. Hover a cell for its evidence."
        ),
        table(headers, [[_contract_cell(item) for item in row] for row in rows]),
    )
