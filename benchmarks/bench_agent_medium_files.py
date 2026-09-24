"""The 32 files of bench_agent's medium-repo fixture, as a frozen snapshot.

Data only: `tally`, a small ledger CLI with one planted bug in its money
parser. What the task asks, the reference fix, the check the agent never
sees and the cheats it must refuse live in bench_agent_medium.py; this
module is kept apart so that neither outgrows the file-size limit.
"""


def _tree(files):
    """Each text starts on the line after its opening quotes; drop that newline.

    A path under the toy repo's docs/ is spelled ./docs/ here and the ./ is
    dropped: the docs gate reads a bare docs/<page>.md in code as a pointer
    into THIS repository's docs.
    """
    return {
        path.removeprefix("./"): text.removeprefix("\n") for path, text in files.items()
    }


FILES = _tree(
    {
        "README.md": r"""
# tally

A small command-line ledger for household spending. It reads a CSV of
entries, prints totals per month or per category, and checks them against a
budget.

## Usage

    python -m tally report examples/march.csv
    python -m tally report examples/march.csv --by category --month 2026-03
    python -m tally budget examples/budget.txt examples/march.csv
    python -m tally categories

The file formats are in ./docs/FORMAT.md.

## Development

    python -m pytest -q
""",
        "CHANGELOG.md": r"""
# Changelog

## 0.3.0
- Refunds: a negative amount reduces the total of its month and category.
- `adjust <category>: <amount>` lines in a budget file.

## 0.2.0
- The `budget` command.
- Category aliases: `groceries` is `food`, `bus` is `transport`.

## 0.1.0
- The `report` command: totals per month or per category.
""",
        "pyproject.toml": r"""
[project]
name = "tally"
version = "0.3.0"
description = "A small household ledger"
requires-python = ">=3.10"

[project.scripts]
tally = "tally.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
""",
        "./docs/FORMAT.md": r"""
# Ledger and budget formats

## Ledger

CSV, one entry per line: `date,amount,category[,note]`.

- `date` is ISO 8601, `2026-03-02`.
- `amount` is euros with at most two decimals. A leading `-` marks a
  refund, which reduces the total of its month and its category.
- `category` is matched case-insensitively and through the aliases in
  `tally/categories.py`.
- A `date,amount,category,note` header, blank lines and `#` comments are
  skipped.

## Budget

One `category: limit` per line, limits in euros. An
`adjust <category>: <amount>` line moves that limit by the amount; a
negative amount tightens it. `#` starts a comment.
""",
        "examples/march.csv": r"""
date,amount,category,note
2026-03-02,12.50,groceries,market
2026-03-05,2.90,bus,ticket
2026-03-09,30.00,food,weekly shop
2026-03-12,-3.05,food,returned bottles
2026-03-20,18.40,restaurant,lunch
""",
        "examples/budget.txt": r"""
# monthly limits in euros
food: 120.00
transport: 40
eating out: 50
adjust food: -10.00
""",
        "tally/__init__.py": r'''
"""tally: a small household ledger."""

__version__ = "0.3.0"
''',
        "tally/__main__.py": r"""
from tally.cli import main

raise SystemExit(main())
""",
        "tally/config.py": r'''
"""Defaults the command line falls back to."""

DEFAULT_GROUP = "month"
ENCODING = "utf-8"
''',
        "tally/errors.py": r'''
"""One base class, so the command line can turn any bad input into exit 2."""


class TallyError(Exception):
    pass


class AmountError(TallyError):
    pass


class DateError(TallyError):
    pass


class CategoryError(TallyError):
    pass


class LedgerError(TallyError):
    pass
''',
        "tally/money.py": r'''
"""Money as integer cents: 0.10 has no exact float, and totals must add up."""

import re

from tally.errors import AmountError

_AMOUNT = re.compile(r"[+-]?\d+(\.\d{1,2})?")


def parse_amount(text):
    """'12.50' -> 1250, '7' -> 700. A leading '-' marks a refund."""
    cleaned = text.strip()
    if not _AMOUNT.fullmatch(cleaned):
        raise AmountError(f"not an amount: {text!r}")
    whole, _, frac = cleaned.partition(".")
    return int(whole) * 100 + int(frac.ljust(2, "0"))


def format_cents(cents):
    """1250 -> '12.50', -305 -> '-3.05'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"
''',
        "tally/dates.py": r'''
"""ISO dates only: a ledger shared between locales cannot guess what 03/04 is."""

import datetime

from tally.errors import DateError


def parse_date(text):
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError as e:
        raise DateError(f"not an ISO date: {text!r}") from e


def month_key(day):
    """datetime.date(2026, 3, 2) -> '2026-03'."""
    return f"{day.year:04d}-{day.month:02d}"
''',
        "tally/entry.py": r'''
"""One ledger line, parsed: the day as a date, the money in cents."""

import datetime
from dataclasses import dataclass

from tally.dates import month_key


@dataclass(frozen=True)
class Entry:
    day: datetime.date
    cents: int
    category: str
    note: str = ""

    @property
    def month(self):
        return month_key(self.day)
''',
        "tally/categories.py": r'''
"""Category names as people type them, folded onto one spelling each."""

from tally.errors import CategoryError

ALIASES = {
    "groceries": "food",
    "supermarket": "food",
    "restaurant": "eating out",
    "bus": "transport",
    "train": "transport",
    "fuel": "transport",
}


def normalise(name):
    """' Groceries ' -> 'food'. An unknown name is kept, in lower case."""
    key = " ".join(name.split()).lower()
    if not key:
        raise CategoryError("empty category")
    return ALIASES.get(key, key)


def known():
    """The categories the aliases lead to, sorted."""
    return sorted(set(ALIASES.values()))
''',
        "tally/ledger.py": r'''
"""Reading a ledger: CSV lines of date,amount,category[,note]."""

import csv

from tally.categories import normalise
from tally.config import ENCODING
from tally.dates import parse_date
from tally.entry import Entry
from tally.errors import LedgerError, TallyError
from tally.money import parse_amount

HEADER = ["date", "amount", "category", "note"]


def read_ledger(lines):
    """Entries in file order; the header, blank lines and # comments are skipped."""
    entries = []
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = [cell.strip() for cell in next(csv.reader([line]))]
        if [cell.lower() for cell in row] == HEADER:
            continue
        entries.append(_entry(number, row))
    return entries


def _entry(number, row):
    if len(row) not in (3, 4):
        raise LedgerError(f"line {number}: expected 3 or 4 fields, got {len(row)}")
    try:
        return Entry(
            day=parse_date(row[0]),
            cents=parse_amount(row[1]),
            category=normalise(row[2]),
            note=row[3] if len(row) == 4 else "",
        )
    except TallyError as e:
        raise LedgerError(f"line {number}: {e}") from e


def load_ledger(path):
    with open(path, encoding=ENCODING) as f:
        return read_ledger(f.read().splitlines())
''',
        "tally/aggregate.py": r'''
"""Totals over entries. Money stays in integer cents until it is printed."""

from collections import defaultdict

GROUPS = ("month", "category")


def totals_by(entries, key):
    """{group: cents} for key 'month' or 'category'."""
    if key not in GROUPS:
        raise ValueError(f"cannot group by {key!r}")
    totals = defaultdict(int)
    for entry in entries:
        totals[getattr(entry, key)] += entry.cents
    return dict(totals)


def grand_total(entries):
    return sum(entry.cents for entry in entries)
''',
        "tally/filters.py": r'''
"""Narrowing a ledger before it is totalled."""


def in_month(entries, month):
    """The entries of one month, given as '2026-03'."""
    return [entry for entry in entries if entry.month == month]
''',
        "tally/table.py": r'''
"""Plain-text tables: the first column left-aligned, the rest right-aligned."""


def format_table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]
    lines = [_line(headers, widths), _line(["-" * w for w in widths], widths)]
    lines += [_line(row, widths) for row in rows]
    return "\n".join(lines)


def _line(cells, widths):
    first = cells[0].ljust(widths[0])
    rest = [cell.rjust(w) for cell, w in zip(cells[1:], widths[1:])]
    return "  ".join([first, *rest]).rstrip()
''',
        "tally/report.py": r'''
"""The `report` command: totals per month or per category."""

from tally.aggregate import grand_total, totals_by
from tally.money import format_cents
from tally.table import format_table


def render_report(entries, by="month"):
    totals = totals_by(entries, by)
    rows = [[group, format_cents(totals[group])] for group in sorted(totals)]
    rows.append(["total", format_cents(grand_total(entries))])
    return format_table([by, "amount"], rows)
''',
        "tally/budget.py": r'''
"""The `budget` command: what each category spent against its limit."""

from tally.aggregate import totals_by
from tally.categories import normalise
from tally.errors import TallyError
from tally.money import format_cents, parse_amount


def read_budget(lines):
    """{category: limit in cents}; 'adjust <category>: <amount>' moves a limit."""
    limits, adjustments = {}, []
    for number, line in enumerate(lines, start=1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        name, sep, amount = text.partition(":")
        if not sep:
            raise TallyError(f"budget line {number}: expected 'category: amount'")
        verb, _, rest = name.partition(" ")
        if verb.lower() == "adjust" and rest.strip():
            adjustments.append((normalise(rest), parse_amount(amount)))
        else:
            limits[normalise(name)] = parse_amount(amount)
    for category, cents in adjustments:
        limits[category] = limits.get(category, 0) + cents
    return limits


def check_budget(entries, limits):
    """One line per budgeted category: spent, limit, and whether it is over."""
    spent = totals_by(entries, "category")
    lines = []
    for category in sorted(limits):
        used, limit = spent.get(category, 0), limits[category]
        verdict = f"OVER by {format_cents(used - limit)}" if used > limit else "ok"
        lines.append(f"{category}: {format_cents(used)} of {format_cents(limit)} {verdict}")
    return lines
''',
        "tally/cli.py": r'''
"""Command line: python -m tally <command> ..."""

import argparse
import sys

from tally import __version__
from tally.budget import check_budget, read_budget
from tally.categories import known
from tally.config import DEFAULT_GROUP, ENCODING
from tally.errors import TallyError
from tally.filters import in_month
from tally.ledger import load_ledger
from tally.report import render_report


def build_parser():
    parser = argparse.ArgumentParser(prog="tally", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="totals per month or per category")
    report.add_argument("ledger")
    report.add_argument("--by", choices=["month", "category"], default=DEFAULT_GROUP)
    report.add_argument("--month", help="only this month, e.g. 2026-03")
    budget = sub.add_parser("budget", help="spending against a budget")
    budget.add_argument("budget")
    budget.add_argument("ledger")
    budget.add_argument("--month", help="only this month, e.g. 2026-03")
    sub.add_parser("categories", help="list the categories the aliases lead to")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "categories":
        print("\n".join(known()))
        return 0
    try:
        entries = load_ledger(args.ledger)
        if args.month:
            entries = in_month(entries, args.month)
        if args.command == "report":
            print(render_report(entries, by=args.by))
            return 0
        with open(args.budget, encoding=ENCODING) as f:
            limits = read_budget(f.read().splitlines())
        print("\n".join(check_budget(entries, limits)))
        return 0
    except (OSError, TallyError) as e:
        print(f"tally: {e}", file=sys.stderr)
        return 2
''',
        "tests/conftest.py": r'''
import pytest


@pytest.fixture
def march_lines():
    """A ledger without refunds: the case every command handled from 0.1.0 on."""
    return [
        "date,amount,category,note",
        "2026-03-02,12.50,groceries,market",
        "2026-03-05,2.90,bus,ticket",
        "2026-03-09,30.00,food,weekly shop",
        "2026-04-01,7.00,train,to work",
    ]


@pytest.fixture
def write(tmp_path):
    def _write(name, lines):
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    return _write
''',
        "tests/test_money.py": r"""
import pytest

from tally.errors import AmountError
from tally.money import format_cents, parse_amount


@pytest.mark.parametrize(
    ("text", "cents"),
    [("12.50", 1250), ("12.5", 1250), ("7", 700), ("0.05", 5), (" 3.20 ", 320)],
)
def test_parse_amount(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "1.234", "1,50", "--1"])
def test_parse_amount_refuses_what_is_not_an_amount(text):
    with pytest.raises(AmountError):
        parse_amount(text)


@pytest.mark.parametrize(
    ("cents", "text"), [(1250, "12.50"), (5, "0.05"), (0, "0.00"), (-305, "-3.05")]
)
def test_format_cents(cents, text):
    assert format_cents(cents) == text
""",
        "tests/test_dates.py": r"""
import datetime

import pytest

from tally.dates import month_key, parse_date
from tally.errors import DateError


def test_parse_date():
    assert parse_date(" 2026-03-02 ") == datetime.date(2026, 3, 2)


@pytest.mark.parametrize("text", ["02.03.2026", "2026-13-01", ""])
def test_parse_date_refuses_what_is_not_iso(text):
    with pytest.raises(DateError):
        parse_date(text)


def test_month_key():
    assert month_key(datetime.date(2026, 3, 2)) == "2026-03"
""",
        "tests/test_categories.py": r"""
import pytest

from tally.categories import known, normalise
from tally.errors import CategoryError


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Groceries", "food"),
        (" eating   out ", "eating out"),
        ("Bus", "transport"),
        ("Books", "books"),
    ],
)
def test_normalise(raw, name):
    assert normalise(raw) == name


def test_an_empty_category_is_refused():
    with pytest.raises(CategoryError):
        normalise("   ")


def test_known_is_sorted_and_unique():
    assert known() == sorted(set(known()))
""",
        "tests/test_ledger.py": r"""
import pytest

from tally.errors import LedgerError
from tally.ledger import load_ledger, read_ledger


def test_entries_come_back_in_file_order(march_lines):
    entries = read_ledger(march_lines)
    assert [e.cents for e in entries] == [1250, 290, 3000, 700]
    assert [e.category for e in entries] == ["food", "transport", "food", "transport"]
    assert entries[0].note == "market"


def test_comments_and_blank_lines_are_skipped():
    entries = read_ledger(["# March", "", "2026-03-02,1.00,food"])
    assert len(entries) == 1 and entries[0].note == ""


def test_a_bad_line_is_named_by_its_number():
    with pytest.raises(LedgerError, match="line 2"):
        read_ledger(["2026-03-02,1.00,food", "2026-03-03,lots,food"])


def test_load_ledger(write, march_lines):
    assert len(load_ledger(write("m.csv", march_lines))) == 4
""",
        "tests/test_aggregate.py": r"""
import datetime

from tally.aggregate import grand_total, totals_by
from tally.entry import Entry


def entry(day, cents, category):
    return Entry(datetime.date(2026, 3, day), cents, category)


ENTRIES = [entry(2, 1250, "food"), entry(5, 290, "transport"), entry(12, -305, "food")]


def test_totals_by_category_net_a_refund():
    assert totals_by(ENTRIES, "category") == {"food": 945, "transport": 290}


def test_totals_by_month():
    assert totals_by(ENTRIES, "month") == {"2026-03": 1235}


def test_grand_total():
    assert grand_total(ENTRIES) == 1235
""",
        "tests/test_filters.py": r"""
import datetime

from tally.entry import Entry
from tally.filters import in_month


def test_in_month_keeps_only_that_month():
    entries = [
        Entry(datetime.date(2026, 3, 31), 100, "food"),
        Entry(datetime.date(2026, 4, 1), 200, "food"),
    ]
    assert [e.cents for e in in_month(entries, "2026-04")] == [200]
""",
        "tests/test_table.py": r"""
from tally.table import format_table


def test_numbers_are_right_aligned():
    text = format_table(["month", "amount"], [["2026-03", "9.45"], ["total", "120.00"]])
    assert text.splitlines() == [
        "month    amount",
        "-------  ------",
        "2026-03    9.45",
        "total    120.00",
    ]
""",
        "tests/test_report.py": r"""
from tally.ledger import read_ledger
from tally.report import render_report


def test_monthly_report(march_lines):
    lines = render_report(read_ledger(march_lines)).splitlines()
    assert lines[2:] == ["2026-03   45.40", "2026-04    7.00", "total     52.40"]


def test_report_by_category(march_lines):
    lines = render_report(read_ledger(march_lines), by="category").splitlines()
    assert lines[0].split() == ["category", "amount"]
    assert lines[2:] == ["food        42.50", "transport    9.90", "total       52.40"]


def test_a_refund_reduces_its_month():
    ledger = [
        "2026-03-02,12.50,groceries,market",
        "2026-03-09,30.00,food,weekly shop",
        "2026-03-12,-3.05,food,returned bottles",
    ]
    lines = render_report(read_ledger(ledger)).splitlines()
    assert lines[2] == "2026-03   39.45"
""",
        "tests/test_budget.py": r"""
from tally.budget import check_budget, read_budget
from tally.ledger import read_ledger


def test_limits_go_through_the_aliases():
    lines = ["# limits", "Groceries: 120", "bus: 40.50"]
    assert read_budget(lines) == {"food": 12000, "transport": 4050}


def test_over_and_under(march_lines):
    limits = {"food": 4000, "transport": 1000}
    assert check_budget(read_ledger(march_lines), limits) == [
        "food: 42.50 of 40.00 OVER by 2.50",
        "transport: 9.90 of 10.00 ok",
    ]


def test_an_adjustment_raises_a_limit():
    assert read_budget(["food: 100", "adjust food: 20.00"]) == {"food": 12000}


def test_a_negative_adjustment_tightens_a_limit():
    limits = read_budget(["food: 50.00", "adjust food: -0.50"])
    spent = read_ledger(["2026-03-02,49.75,food"])
    assert check_budget(spent, limits) == ["food: 49.75 of 49.50 OVER by 0.25"]
""",
        "tests/test_cli.py": r"""
from tally.cli import main


def test_report_for_one_month(write, march_lines, capsys):
    assert main(["report", write("m.csv", march_lines), "--month", "2026-03"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "total     45.40"


def test_budget(write, march_lines, capsys):
    budget = write("b.txt", ["food: 50", "transport: 5"])
    assert main(["budget", budget, write("m.csv", march_lines)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "food: 42.50 of 50.00 ok",
        "transport: 9.90 of 5.00 OVER by 4.90",
    ]


def test_bad_input_exits_2_and_names_the_line(write, capsys):
    assert main(["report", write("m.csv", ["2026-03-02,oops,food"])]) == 2
    assert "line 1" in capsys.readouterr().err


def test_categories(capsys):
    assert main(["categories"]) == 0
    assert "food" in capsys.readouterr().out.splitlines()
""",
    }
)
