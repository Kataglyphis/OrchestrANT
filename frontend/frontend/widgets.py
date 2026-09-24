"""The viewer's building blocks: a card, a table and its cells.

Shared by the page (``benchmarks``) and the lab cards (``lab_cards``), which is
why they live apart from both: the lab cards are imported BY the page, so a
helper defined in the page module could not be imported back without a cycle.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

CARD = {
    "border": "1px solid var(--gray-a5)",
    "border_radius": "10px",
    "padding": "1rem 1.25rem",
    "width": "100%",
}


def cell(text: Any, *, header: bool = False, title: Any = None) -> rx.Component:
    return (
        rx.el.th(text, title=title, py="2", px="3", text_align="left")
        if header
        else rx.el.td(
            text, title=title, py="2", px="3", border_top="1px solid var(--gray-a4)"
        )
    )


def table(headers: list[tuple[str, str | None]], rows: Any) -> rx.Component:
    return rx.el.table(
        rx.el.thead(
            rx.el.tr(
                *[cell(name, header=True, title=tip) for name, tip in headers],
                background="var(--gray-a3)",
            )
        ),
        rx.el.tbody(rows),
        width="100%",
        font_size="13px",
        border_collapse="collapse",
    )


def card(*children: rx.Component, **props: Any) -> rx.Component:
    """A bordered box; `props` override CARD (drill_down passed width= to a
    card() that took none, a TypeError the first time the page compiled)."""
    return rx.box(*children, **{**CARD, **props})


def note(*parts: Any, color: str = "var(--gray-11)") -> rx.Component:
    """The 12px caption under a card's heading."""
    return rx.text(*parts, font_size="12px", color=color)
