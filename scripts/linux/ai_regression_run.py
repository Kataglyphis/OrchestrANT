#!/usr/bin/env python3
"""ai-regression.yml's model runs inside :latest: the text-generation-v1 replay, the two-cats detector and latency rows.

CPU provider only; the shell driver serves the models for `orchestrant-bench contract` and grades through ai_regression_verdicts.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from ai_regression_verdicts import (
    percentile_row,
    read_json,
    replay_problems,
    speed_rows,
)

from orchestrant.benchmark.client import write_report
from orchestrant.genai.engine import GenaiEngine, GenerationOptions
from orchestrant.yolo import providers


if TYPE_CHECKING:
    from types import ModuleType


postprocess = importlib.import_module("orchestrant.yolo.core.postprocess")
preprocess = importlib.import_module("orchestrant.yolo.core.preprocess")
DIAGNOSTIC = Path(__file__).resolve().parents[1] / "diagnostics/detector_parity.py"
TIMED_RUNS = 20


def load_diagnostic() -> ModuleType:
    """Load the packaged detector diagnostic, whose capture() is the production pre/postprocess path."""
    spec = importlib.util.spec_from_file_location(
        "detector_parity_diagnostic", DIAGNOSTIC
    )
    if spec is None or spec.loader is None:
        msg = f"cannot load {DIAGNOSTIC}"
        raise ValueError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay(args: argparse.Namespace) -> int:
    """Greedy-decode every case of the contract on the CPU provider."""
    contract = read_json(Path(args.contract))
    cases = contract.get("cases") if isinstance(contract, dict) else None
    if not isinstance(cases, list) or not cases:
        msg = f"{args.contract} holds no cases"
        raise ValueError(msg)
    problems = []
    for case in cases:
        engine = GenaiEngine(Path(args.models) / case["model"], "cpu")
        options = GenerationOptions(
            max_new_tokens=case["max_new_tokens"], temperature=0.0
        )
        result = engine.generate(case["messages"], options)
        problems += replay_problems(case, result.tokens, result.text)
        sys.stdout.write(
            f"replay {case['name']}: {result.text!r} ids={result.tokens}\n"
        )
    for line in problems:
        sys.stderr.write(f"::error title=text-generation-v1 replay failed::{line}\n")
    return 1 if problems else 0


def detector(args: argparse.Namespace) -> int:
    """Write two production-path results for compare_detector_parity.py and the timed runs."""
    diagnostic = load_diagnostic()
    diagnostic.check_hash(Path(args.fixture), diagnostic.FIXTURE_SHA256)
    frame = cv2.imread(args.fixture, cv2.IMREAD_COLOR)
    if frame is None:
        msg = f"cannot decode {args.fixture}"
        raise ValueError(msg)
    session = providers.create_session(args.model, 0, "cpu")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    results = diagnostic.capture(session, frame, np, preprocess, postprocess)
    for index, result in enumerate(results, 1):
        text = json.dumps(result, allow_nan=False) + "\n"
        (out / f"detection-{index}.json").write_text(text, encoding="utf-8")
    samples = []
    for _ in range(TIMED_RUNS):
        start = time.perf_counter_ns()
        diagnostic.capture(session, frame, np, preprocess, postprocess)
        samples.append((time.perf_counter_ns() - start) / 2e6)
    (out / "latency-ms.json").write_text(json.dumps(samples) + "\n", encoding="utf-8")
    return 0


def latency(args: argparse.Namespace) -> int:
    """Write the p50/p95 rows as one orchestrant-bench report for bench-history."""
    rows = []
    for spec in args.speed:
        name, _, path = spec.partition("=")
        rows += speed_rows(name, read_json(Path(path)))
    samples = read_json(Path(args.detector))
    if not isinstance(samples, list):
        msg = f"{args.detector} is not a list of milliseconds"
        raise TypeError(msg)
    rows.append(percentile_row("detector_two_cats", samples))
    config = {"timed_runs": TIMED_RUNS}
    write_report(args.out, "ai-regression", config, rows, None, [__file__])
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Return the replay, detector or latency command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay_p = sub.add_parser("replay")
    replay_p.add_argument("--contract", required=True)
    replay_p.add_argument("--models", required=True)
    detector_p = sub.add_parser("detector")
    detector_p.add_argument("--model", required=True)
    detector_p.add_argument("--fixture", required=True)
    detector_p.add_argument("--out", required=True)
    latency_p = sub.add_parser("latency")
    latency_p.add_argument("--speed", action="append", default=[], metavar="NAME=FILE")
    latency_p.add_argument("--detector", required=True)
    latency_p.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one command; 2 on unreadable input."""
    args = parse_args(argv)
    commands = {"replay": replay, "detector": detector, "latency": latency}
    try:
        return commands[args.command](args)
    except (TypeError, ValueError) as exc:
        sys.stderr.write(f"::error title=ai-regression input::{exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
