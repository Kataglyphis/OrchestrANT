#!/usr/bin/env python3
"""One --perf-host run: a warm detector or text generation in one process, printed as one JSON line.

It times the CPU provider only, on one image and one prompt; quality-report.toml declares the runs.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from ai_regression_run import load_diagnostic

from orchestrant.genai.engine import GenaiEngine, GenerationOptions
from orchestrant.yolo import providers


postprocess = importlib.import_module("orchestrant.yolo.core.postprocess")
preprocess = importlib.import_module("orchestrant.yolo.core.preprocess")
WARM_CAPTURES = 5
TIMED_CAPTURES = 40
TIMED_REPLIES = 5
TEXT_TOKENS = 48
TEXT_PROMPT = "Tell a short story about two cats on a sofa."


def percentile(samples: list[float], fraction: float) -> float:
    """The nearest-rank percentile of ``samples``."""
    ranked = sorted(samples)
    return ranked[min(len(ranked) - 1, int(fraction * len(ranked)))]


def detector(args: argparse.Namespace) -> dict[str, float]:
    """p50 and p95 of the production capture path, per frame, in a warm session."""
    diagnostic = load_diagnostic()
    frame = cv2.imread(args.fixture, cv2.IMREAD_COLOR)
    if frame is None:
        msg = f"cannot decode {args.fixture}"
        raise ValueError(msg)
    session = providers.create_session(
        args.model, 0, "cpu", intra_op_threads=args.threads
    )
    for _ in range(WARM_CAPTURES):
        diagnostic.capture(session, frame, np, preprocess, postprocess)
    samples = []
    for _ in range(TIMED_CAPTURES):
        start = time.perf_counter_ns()
        diagnostic.capture(session, frame, np, preprocess, postprocess)
        samples.append((time.perf_counter_ns() - start) / 2e6)
    return {"p50_ms": statistics.median(samples), "p95_ms": percentile(samples, 0.95)}


def text(args: argparse.Namespace) -> dict[str, float]:
    """Median time to first token and decode rate of greedy replies, after one warming reply."""
    engine = GenaiEngine(Path(args.model), "cpu")
    messages = [{"role": "user", "content": TEXT_PROMPT}]
    options = GenerationOptions(max_new_tokens=TEXT_TOKENS, temperature=0.0)
    engine.generate(messages, options)
    results = [engine.generate(messages, options) for _ in range(TIMED_REPLIES)]
    return {
        "ttft_ms": statistics.median(r.time_to_first_token_ms for r in results),
        "decode_tok_s": statistics.median(r.decode_tokens_per_second for r in results),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Return the detector or text command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    detector_p = sub.add_parser("detector")
    detector_p.add_argument("--model", required=True)
    detector_p.add_argument("--fixture", required=True)
    detector_p.add_argument(
        "--threads",
        type=int,
        default=None,
        help="0 is ORT's default; none the shipped one",
    )
    text_p = sub.add_parser("text")
    text_p.add_argument("--model", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print one run's figures as the last stdout line."""
    args = parse_args(argv)
    figures = detector(args) if args.command == "detector" else text(args)
    sys.stdout.write(json.dumps(figures) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
