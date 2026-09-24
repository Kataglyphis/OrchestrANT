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

from orchestrant.benchmark import speed_summary
from orchestrant.benchmark.answers import answered_count, row_answer_s


def _rows(doc):
    """Every per-prompt result, errors included, whichever envelope the file uses."""
    if "results" in doc:
        return list(doc["results"])
    return [row for rep in doc.get("reports", []) for row in rep.get("results", [])]


def _mean(values):
    return sum(values) / len(values) if values else None


def summarise(doc):
    """One line's worth of numbers for a single result file.

    The token, rate and TTFT figures are speed_summary's -- the ones the speed
    runner's own table prints -- so `summary`, `table` and the runner cannot
    print two numbers under one name again.
    """
    rows = _rows(doc)
    ok = speed_summary.completed(rows)
    if not ok:
        return None
    return {
        **speed_summary.summarise(rows),
        # Rows cut at max_tokens have no time to an answer (answers.py), so
        # the mean travels with how many rows it covers.
        "answer_s": _mean([s for s in map(row_answer_s, ok) if s is not None]),
        "answered": answered_count(ok),
        "cpu_percent": _mean([r["cpu_percent"] for r in ok if "cpu_percent" in r]),
        "ram_used_gb": _mean([r["ram_used_gb"] for r in ok if "ram_used_gb" in r]),
        "gpu_utilization_percent": _mean(
            [
                r["gpu_utilization_percent"]
                for r in ok
                if r.get("gpu_utilization_percent") is not None
            ]
        ),
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
    records = []
    for path in result_files(directory):
        with open(path) as f:
            doc = json.load(f)
        records.append((doc.get("hardware"), doc.get("provenance")))
        entry = {
            "label": os.path.basename(path)[:-5],
            "file": os.path.basename(path),
            "kind": report_kind(doc),
            "config": doc.get("config", {}),
            "correctness": doc.get("correctness"),
            "results": doc.get("results", []),
            **run_fields(doc),
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
                    # A count of cases observed to pass; the viewer used to
                    # round passed * n / total into one, which is wrong when
                    # attempts per case are uneven.
                    "effective_k": r.get("effective_k"),
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
    manifest["host_hardware"] = host_hardware(records)
    return manifest


def host_hardware(records):
    """The Hardware card's record: the first real `hardware` block.

    `records` is (hardware, provenance) per file. A provenance block names the
    OS and host but no cores, threads or RAM, so it stands in only when no
    report carries hardware. It used to win whenever its file sorted first,
    and a contract report sorts before the speed run beside it: the tracked
    2026-09-23 run's card read "? cores / ? threads" and "? GB", with 8
    threads and 31.6 GB recorded one file later.
    """
    for which in (0, 1):
        for record in records:
            if isinstance(record[which], dict) and record[which]:
                return record[which]
    return {}


def run_fields(doc):
    """What the viewer shows per run beyond its rows, which live in `results`.

    The serving build and its flags (`provenance.runtime`; on v0.7.0 `--log
    info` alone cost the NPU lane 13 % of its decode, so a run means little
    without them), the energy block whose `net_reliable` says whether net
    joules can be read, and the thread count that derives `other_cores` for a
    report older than the field. The manifest used to carry the first file's
    hardware and nothing per run, so the viewer could not tell two builds apart.
    The timestamp orders one lane's contract runs: file names need not sort by
    date, and "what the upgrade changed" is read against the run before.
    `speed` is the run's headline figures from speed_summary, the runner's
    own: the viewer used to average the rows its own way and chart 18.3 tok/s
    as "overall" where the runner printed 25.4 under that name.
    """
    provenance = doc.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    hardware = doc.get("hardware")
    if not isinstance(hardware, dict):
        hardware = {}
    return {
        "backend": doc.get("backend"),
        "model": doc.get("model"),
        # The runtime is of THIS endpoint only: a lanes report spans two lanes
        # and records its first one's build.
        "base_url": provenance.get("base_url") or doc.get("api_url"),
        "runtime": provenance.get("runtime"),
        "energy": doc.get("energy"),
        "cpu_threads": hardware.get("cpu_total_threads"),
        "timestamp": provenance.get("timestamp_utc") or doc.get("timestamp"),
        "speed": speed_summary.summarise(_rows(doc)),
    }


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


def _answer(s, spec):
    """Mean seconds to an answer, with "(k/n)" whenever a reply was cut."""
    k, n = s.get("answered") or (None, 0)
    cut = f" ({k}/{n} answered)" if n and k < n else ""
    return f"{_fmt(s['answer_s'], spec)}s{cut}"


def summary_line(s):
    """`report summary`: one run's figures, printed after each run of a sweep.

    Decode, Overall and TTFT under the names, and with the numbers, of the
    speed runner's own summary (speed_summary).
    """
    return (
        f"  -> Decode: {_fmt(s['decode_tok_s'], '.1f')} tok/s  "
        f"Overall: {_fmt(s['overall_tok_s'], '.1f')} tok/s  "
        f"Answer: {_answer(s, '.1f')} avg  "
        f"CPU: {_fmt(s['cpu_percent'], '.1f')}%  "
        f"RAM: {_fmt(s['ram_used_gb'], '.1f')}GB"
        + (f"  TTFT: {s['ttft_s']:.2f}s avg" if s["ttft_s"] is not None else "")
        + (
            f"  GPU: {s['gpu_utilization_percent']:.1f}%"
            if s["gpu_utilization_percent"] is not None
            else ""
        )
        + (f"  ERRORS: {s['errored']}" if s["errored"] else "")
    )


def table_line(name, s):
    """`report table`: one result file per line, the figures of summary_line."""
    return (
        f"  {name:25s}  Decode: {_fmt(s['decode_tok_s'], '5.1f')}  "
        f"Overall: {_fmt(s['overall_tok_s'], '5.1f')}  "
        f"TTFT: {_fmt(s['ttft_s'], '5.2f')}s  "
        f"Answer: {_answer(s, '5.1f')}  "
        f"CPU: {_fmt(s['cpu_percent'], '5.1f')}%  "
        f"CT: {s['completion_tokens']:4d}  PT: {s['prompt_tokens']:4d}"
        + (f"  ERRORS: {s['errored']}" if s["errored"] else "")
    )


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
        print(summary_line(s))
        return

    if args.cmd == "manifest":
        m = build_manifest(args.directory, args.title, args.model, args.generated)
        with open(args.out, "w") as f:
            json.dump(m, f, indent=2)
        print(f"  Manifest: {args.out} ({len(m['configs'])} configs)")
        return

    for name, s in comparison_rows(args.directory):
        print(table_line(name, s))


if __name__ == "__main__":
    main()
