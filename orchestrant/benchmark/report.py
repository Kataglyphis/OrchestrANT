#!/usr/bin/env python3
"""Summaries and the viewer manifest, lifted out of run_benchmarks.sh.

These three programs lived as heredocs inside the shell script: unreachable
from pytest, un-lintable, and quoting-fragile (a stray `"` inside one would
break the surrounding shell, not raise a Python error). One of them had
already grown a defensive comment about a KeyError that "killed the whole
comparison under set -e at the end of every multi-hour run" — a bug that a
five-line unit test would have caught before the run rather than after.

Usage:
    python3 bench_report.py summary  <result.json>
    python3 bench_report.py manifest <dir> <out.json> --title T --model M --generated TS
    python3 bench_report.py table    <dir>
"""

import argparse
import glob
import json
import os
import sys


def _ok_results(doc):
    """Successful per-prompt results, whichever envelope the file uses."""
    if "results" in doc:
        return [r for r in doc["results"] if "error" not in r]
    out = []
    for rep in doc.get("reports", []):
        out.extend(r for r in rep.get("results", []) if "error" not in r)
    return out


def _mean(values):
    return sum(values) / len(values) if values else None


def summarise(doc):
    """One line's worth of numbers for a single result file."""
    ok = _ok_results(doc)
    if not ok:
        return None
    ttfts = [r["ttft_s"] for r in ok if r.get("ttft_s") is not None]
    return {
        "n": len(ok),
        "tokens_per_sec": _mean(
            [r["tokens_per_sec"] for r in ok if "tokens_per_sec" in r]
        ),
        "answer_s": _mean(
            [
                r.get("wall_s_to_answer", r.get("latency_s"))
                for r in ok
                if r.get("wall_s_to_answer") or r.get("latency_s")
            ]
        ),
        "ttft_s": _mean(ttfts) if ttfts else None,
        "cpu_percent": _mean([r["cpu_percent"] for r in ok if "cpu_percent" in r]),
        "ram_used_gb": _mean([r["ram_used_gb"] for r in ok if "ram_used_gb" in r]),
        "gpu_utilization_percent": _mean(
            [
                r["gpu_utilization_percent"]
                for r in ok
                if r.get("gpu_utilization_percent") is not None
            ]
        ),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in ok),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in ok),
    }


def result_files(directory):
    """Result files in `directory`, excluding our own generated ones.

    The leading-underscore rule is load-bearing: _manifest.json has no
    `results` key and sorts FIRST, so without this guard the KeyError killed
    the whole comparison under `set -e` at the end of a multi-hour run.
    """
    return sorted(
        f
        for f in glob.glob(os.path.join(directory, "*.json"))
        if not os.path.basename(f).startswith("_")
    )


def build_manifest(directory, title, model, generated):
    """Index every result file for the viewer.

    Handles both envelopes. The viewer showed only the throughput tool for as
    long as it existed, so coding, tool-calling and lane results were invisible
    in the one place a person actually looks at them; `kind` lets it render each
    for what it is instead of forcing one shape onto all three.
    """
    manifest = {
        "title": title,
        "generated": generated,
        "model": model,
        "host_hardware": {},
        "configs": [],
    }
    for path in result_files(directory):
        with open(path) as f:
            doc = json.load(f)
        hw = doc.get("hardware") or doc.get("provenance") or {}
        if hw and not manifest["host_hardware"]:
            manifest["host_hardware"] = hw
        entry = {
            "label": os.path.basename(path)[:-5],
            "file": os.path.basename(path),
            "kind": report_kind(doc),
            "config": doc.get("config", {}),
            "correctness": doc.get("correctness"),
            "results": doc.get("results", []),
        }
        if entry["kind"] == "unknown":
            print(
                f"  WARNING: {path} has neither 'benchmark' nor 'results' — "
                f"not a report this suite wrote",
                file=sys.stderr,
            )
        if "reports" in doc:
            # Scored benchmarks keep their scores AND flattened per-case rows.
            # A row with no integer passed/total is not a score ("/ = 0%").
            scored = [
                {
                    "label": r.get("label"),
                    "model": r.get("model"),
                    "passed": r.get("passed"),
                    "total": r.get("total"),
                    "effective_n": r.get("effective_n"),
                    "deterministic": r.get("deterministic"),
                    "truncated": r.get("truncated"),
                    "errored": r.get("errored"),
                    "total_wall_s": r.get("total_wall_s"),
                    "median_wall_s": r.get("median_wall_s"),
                    "results": r.get("results", []),
                }
                for r in doc["reports"]
                if is_scored(r)
            ]
            unscored = [
                {k: v for k, v in r.items() if k != "results"}
                for r in doc["reports"]
                if not is_scored(r)
            ]
            if scored:
                entry["scored"] = scored
            if unscored:
                entry["unscored"] = unscored
            entry["results"] = [
                row for r in doc["reports"] for row in r.get("results", [])
            ]
        manifest["configs"].append(entry)
    return manifest


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_scored(row):
    """A report row is a score only when passed AND total are integer counts."""
    return _count(row.get("passed")) and _count(row.get("total"))


def report_kind(doc):
    """'benchmark' from the shared envelope; 'throughput' for the legacy shape;
    'unknown' for a JSON that is neither, so the viewer never fabricates a row.
    """
    if doc.get("benchmark"):
        return doc["benchmark"]
    return "throughput" if "results" in doc else "unknown"


def comparison_rows(directory):
    rows = []
    for path in result_files(directory):
        with open(path) as f:
            doc = json.load(f)
        s = summarise(doc)
        if s:
            rows.append((os.path.basename(path)[:-5], s))
    return rows


def _fmt(value, spec, missing="    -"):
    return format(value, spec) if isinstance(value, (int, float)) else missing


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary")
    p.add_argument("file")
    p = sub.add_parser("manifest")
    p.add_argument("directory")
    p.add_argument("out")
    p.add_argument("--title", default="LLM Benchmark")
    p.add_argument("--model", default="")
    p.add_argument("--generated", default="")
    p = sub.add_parser("table")
    p.add_argument("directory")
    args = ap.parse_args()

    if args.cmd == "summary":
        with open(args.file) as f:
            s = summarise(json.load(f))
        if not s:
            print("  -> no successful results")
            return
        print(
            f"  -> T/s: {_fmt(s['tokens_per_sec'], '.1f')} avg  "
            f"Answer: {_fmt(s['answer_s'], '.1f')}s avg  "
            f"CPU: {_fmt(s['cpu_percent'], '.1f')}%  "
            f"RAM: {_fmt(s['ram_used_gb'], '.1f')}GB"
            + (f"  TTFT: {s['ttft_s']:.2f}s avg" if s["ttft_s"] else "")
            + (
                f"  GPU: {s['gpu_utilization_percent']:.1f}%"
                if s["gpu_utilization_percent"] is not None
                else ""
            )
        )
        return

    if args.cmd == "manifest":
        m = build_manifest(args.directory, args.title, args.model, args.generated)
        with open(args.out, "w") as f:
            json.dump(m, f, indent=2)
        print(f"  Manifest: {args.out} ({len(m['configs'])} configs)")
        return

    for name, s in comparison_rows(args.directory):
        print(
            f"  {name:25s}  T/s: {_fmt(s['tokens_per_sec'], '5.1f')}  "
            f"TTFT: {_fmt(s['ttft_s'], '5.2f')}s  "
            f"Answer: {_fmt(s['answer_s'], '5.1f')}s  "
            f"CPU: {_fmt(s['cpu_percent'], '5.1f')}%  "
            f"CT: {s['completion_tokens']:4d}  PT: {s['prompt_tokens']:4d}"
        )


if __name__ == "__main__":
    main()
