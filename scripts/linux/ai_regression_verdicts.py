#!/usr/bin/env python3
"""ai-regression.yml's verdicts: contract answers, the text-generation-v1 replay and the latency rows.

It grades no wall time and no yes, no, inconclusive or skipped answer; ai_regression_run.py produces the inputs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def read_json(path: Path) -> object:
    """Parse one JSON file; a missing or malformed one raises ValueError."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"{path}: {exc}"
        raise ValueError(msg) from None


def contract_checks(report: object) -> list[dict]:
    """Return the check rows of one `orchestrant-bench contract --output` report."""
    reports = report.get("reports") if isinstance(report, dict) else None
    rows = reports[0].get("checks") if isinstance(reports, list) and reports else None
    if not isinstance(rows, list) or not rows:
        msg = "the report holds no contract checks"
        raise ValueError(msg)
    return rows


def grade(reports: dict[str, list[dict]]) -> tuple[list[str], list[str]]:
    """Return a Markdown table of every answer and one line per `error` answer."""
    ids = list(dict.fromkeys(row["id"] for rows in reports.values() for row in rows))
    table = [
        "| Check | " + " | ".join(reports) + " |",
        "| --- |" + " --- |" * len(reports),
    ]
    answers = {name: {row["id"]: row for row in rows} for name, rows in reports.items()}
    errors = []
    for check in ids:
        cells = []
        for name in reports:
            row = answers[name].get(check, {})
            answer = str(row.get("answer", "-"))
            cells.append(answer)
            if answer == "error":
                errors.append(f"{name}/{check}: {str(row.get('evidence', ''))[:200]}")
        table.append(f"| `{check}` | " + " | ".join(cells) + " |")
    return table, errors


def replay_problems(case: dict, tokens: list[int], text: str) -> list[str]:
    """Return what differs from a text-generation-v1 case: its greedy ids or its required text."""
    problems = []
    if list(tokens) != list(case["expected_ids"]):
        problems.append(
            f"{case['name']}: ids {list(tokens)} != expected {case['expected_ids']}"
        )
    if case["must_contain"] not in text:
        problems.append(f"{case['name']}: {text!r} lacks {case['must_contain']!r}")
    return problems


def percentile_row(label: str, samples_ms: list[float]) -> dict:
    """Return one orchestrant-bench report row with the p50 and p95 bench-history reads."""
    if len(samples_ms) < 2:
        msg = f"{label}: {len(samples_ms)} sample(s), at least 2 are needed"
        raise ValueError(msg)
    return {
        "label": label,
        "unit": "ms",
        "better": "lower",
        "rounds": len(samples_ms),
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": statistics.quantiles(samples_ms, n=20, method="inclusive")[-1],
        "results": [{"wall_ms": s} for s in samples_ms],
    }


def speed_rows(name: str, report: object) -> list[dict]:
    """Return request latency and first-token rows of an `orchestrant-bench speed` report."""
    rows = report.get("results", []) if isinstance(report, dict) else []
    ok = [r for r in rows if isinstance(r, dict) and "error" not in r]
    latency = [r["latency_s"] * 1000 for r in ok if r.get("latency_s") is not None]
    ttft = [r["ttft_s"] * 1000 for r in ok if r.get("ttft_s") is not None]
    return [
        percentile_row(f"{name}_request", latency),
        percentile_row(f"{name}_ttft", ttft),
    ]


def main(argv: list[str] | None = None) -> int:
    """Grade contract reports: 1 on an `error` answer, 2 on an unreadable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="contract-<name>.json reports")
    parser.add_argument("--summary", required=True, help="the Markdown table to write")
    args = parser.parse_args(argv)
    try:
        reports = {
            Path(p).stem.removeprefix("contract-"): contract_checks(read_json(Path(p)))
            for p in args.reports
        }
    except ValueError as exc:
        sys.stderr.write(f"::error title=ai-regression input::{exc}\n")
        return 2
    table, errors = grade(reports)
    text = "\n".join(["### Contract answers", "", *table, ""])
    Path(args.summary).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    for line in errors:
        sys.stderr.write(f"::error title=Contract check answered error::{line}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
