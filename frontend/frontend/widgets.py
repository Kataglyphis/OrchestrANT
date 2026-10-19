"""The viewer's building blocks: a card, a table and its cells.

Shared by the page (``benchmarks``) and the lab cards (``lab_cards``), which is
why they live apart from both: the lab cards are imported BY the page, so a
helper defined in the page module could not be imported back without a cycle.
"""

from __future__ import annotations

from typing import Any

import flet as ft


MUTED = ft.Colors.ON_SURFACE_VARIANT
GOOD = ft.Colors.GREEN
WARN = ft.Colors.ORANGE
BAD = ft.Colors.ERROR
MONO = "monospace"

Headers = list[tuple[str | ft.Control, str | None]]


def shown(value: Any) -> str:
    """A cell's text: an absent value is blank, never "None"."""
    return "" if value is None else str(value)


def text(
    value: Any,
    *,
    size: float = 13,
    color: str | None = None,
    tooltip: str | None = None,
    bold: bool = False,
    mono: bool = False,
) -> ft.Text:
    """A selectable cell or caption; `mono` for identifiers, `tooltip` for what hover reveals."""
    return ft.Text(
        shown(value),
        size=size,
        color=color,
        tooltip=tooltip or None,
        weight=ft.FontWeight.BOLD if bold else None,
        font_family=MONO if mono else None,
        selectable=True,
    )


def clipped(value: Any, *, width: float = 260) -> ft.Container:
    """Long free text (a prompt, a model name) on one line; the tooltip holds all of it."""
    full = shown(value)
    return ft.Container(
        content=ft.Text(full, size=13, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        width=width,
        tooltip=full or None,
    )


def heading(value: str, *, size: float = 18) -> ft.Text:
    """A card's title."""
    return ft.Text(value, size=size, weight=ft.FontWeight.BOLD)


def note(value: str, *, color: str | None = MUTED) -> ft.Text:
    """The 12px caption under a card's heading."""
    return ft.Text(value, size=12, color=color)


def card(*children: ft.Control | None) -> ft.Container:
    """A bordered box; a None child is a part with nothing to show, left out."""
    return ft.Container(
        content=ft.Column([c for c in children if c is not None], spacing=10),
        padding=ft.Padding.symmetric(vertical=16, horizontal=20),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=ft.BorderRadius.all(10),
    )


def _cell(value: Any) -> ft.DataCell:
    return ft.DataCell(content=value if isinstance(value, ft.Control) else text(value))


def table(headers: Headers, rows: list[list[Any]]) -> ft.Row:
    """A header-tooltipped table that scrolls sideways inside its card when wide."""
    columns = [
        ft.DataColumn(
            label=label if isinstance(label, ft.Control) else text(label, bold=True),
            tooltip=tip,
        )
        for label, tip in headers
    ]
    grid = ft.DataTable(
        columns=columns,
        rows=[ft.DataRow(cells=[_cell(value) for value in row]) for row in rows],
        heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        heading_row_height=40,
        data_row_min_height=32,
        column_spacing=20,
    )
    return ft.Row([grid], scroll=ft.ScrollMode.AUTO)
