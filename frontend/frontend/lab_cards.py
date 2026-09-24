"""The cards for what the lab records since 2026-09-24.

Per run: whether answers arrived, the load and CPU-rail energy they cost, and
the server build that served them; across runs, the contract probe as a
check x run grid. Each card takes vars ``lab_data`` already shaped, so this
module only lays out -- and it never imports the page, which imports it.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from frontend.widgets import card, cell, note, table

_MUTED = "var(--gray-11)"

_LAB_HEADERS: list[tuple[str, str | None]] = [
    ("Run", None),
    ("Answered", "Replies finished inside max_tokens"),
    (
        "First answer (s)",
        (
            "Mean time to the first answer token, after any thinking, over the "
            "answered replies"
        ),
    ),
    ("Think", "Share of the output spent thinking"),
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

_RUNTIME_HEADERS: list[tuple[str, str | None]] = [
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


def _scroll(child: rx.Component) -> rx.Component:
    """A wide table scrolls inside its card rather than widening the page."""
    return rx.box(child, overflow_x="auto", width="100%")


def _net_color(state: Any) -> Any:
    return rx.match(
        state,
        ("reliable", "var(--green-11)"),
        ("drifted", "var(--orange-11)"),
        _MUTED,
    )


def lab_card(rows: Any) -> rx.Component:
    """Answered k/n, first answer, thinking, lane and other load, joules."""
    return rx.cond(
        rows.length() > 0,
        card(
            rx.heading("Answers, load and energy", size="4"),
            note(
                "A reply cut at max_tokens is not an answer, and its time to one was "
                "not measured. Energy is the CPU-cluster rails only -- an NPU lane's "
                "own draw has no rail -- per completion token, as a ratio of sums. "
                "A CPU lane's speed is conditional on the other load beside it."
            ),
            _scroll(table(_LAB_HEADERS, rx.foreach(rows, _lab_row))),
        ),
    )


def _lab_row(row: Any) -> rx.Component:
    return rx.el.tr(
        cell(rx.code(row["label"])),
        cell(
            rx.text(
                row["answered"],
                color=rx.cond(row["cut"], "var(--orange-11)", "inherit"),
            )
        ),
        cell(row["ttfa"]),
        cell(row["think"]),
        cell(row["lane_cores"]),
        cell(row["other_cores"]),
        cell(row["j_gross"]),
        cell(row["j_net"]),
        cell(
            rx.text(row["net_text"], color=_net_color(row["net_state"])),
            title=row["net_note"],
        ),
        cell(row["watts"]),
    )


def runtime_card(rows: Any) -> rx.Component:
    """Which build served each run, and the flags its lane was launched with."""
    return rx.cond(
        rows.length() > 0,
        card(
            rx.heading("Serving runtime", size="4"),
            note(
                "Two runs of one lane on different builds or flags are different "
                "measurements: on GenieX v0.7.0, --log info alone cost the NPU lane "
                "13 % of its decode rate. 'installed binary' means the lane process "
                "was not visible (a WSL2 client), so the build is the installed one, "
                "not proven to be the one serving."
            ),
            _scroll(
                table(
                    _RUNTIME_HEADERS,
                    rx.foreach(
                        rows,
                        lambda row: rx.el.tr(
                            cell(rx.code(row["label"])),
                            cell(row["kind"]),
                            cell(row["lane"]),
                            cell(row["model"], title=row["model"]),
                            cell(row["endpoint"]),
                            cell(row["runtime"]),
                            cell(row["seen"], title=row["source"]),
                            cell(rx.code(row["flags"])),
                        ),
                    ),
                )
            ),
        ),
    )


def _contract_cell(item: Any) -> rx.Component:
    changed = item["moved"] == "yes"
    return cell(
        rx.cond(
            item["state"] == "check",
            rx.code(item["text"]),
            rx.text(
                item["text"],
                weight=rx.cond(changed, "bold", "regular"),
                color=rx.cond(
                    changed,
                    "var(--violet-11)",
                    rx.cond(item["state"] == "error", "var(--red-11)", "inherit"),
                ),
            ),
        ),
        title=item["tip"],
    )


def _contract_header(column: Any) -> rx.Component:
    return cell(
        rx.vstack(
            rx.text(column["title"]),
            rx.text(column["file"], font_size="11px", color=_MUTED),
            spacing="0",
        ),
        header=True,
        title=column["tip"],
    )


def contract_card(columns: Any, rows: Any) -> rx.Component:
    """`orchestrant-bench contract` as one check x run grid, per lane / version."""
    return rx.cond(
        columns.length() > 0,
        card(
            rx.heading("Server contract", size="4"),
            note(
                "What each lane does, asked of the lane itself (orchestrant-bench "
                "contract). Highlighted: the answer moved since the lane's previous "
                "run -- what an upgrade changed. Hover a cell for its evidence."
            ),
            _scroll(
                rx.el.table(
                    rx.el.thead(
                        rx.el.tr(
                            cell("Check", header=True),
                            rx.foreach(columns, _contract_header),
                            background="var(--gray-a3)",
                        )
                    ),
                    rx.el.tbody(
                        rx.foreach(
                            rows,
                            lambda row: rx.el.tr(rx.foreach(row, _contract_cell)),
                        )
                    ),
                    width="100%",
                    font_size="13px",
                    border_collapse="collapse",
                )
            ),
        ),
    )
