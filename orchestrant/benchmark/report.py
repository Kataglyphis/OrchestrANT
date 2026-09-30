"""Summaries, tables and the viewer manifest over speed-run result files.

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

from orchestrant.benchmark import correctness, speed_summary
from orchestrant.benchmark.answers import answered_count, row_answer_s


def _rows(doc):
    """Every per-prompt result, errors included, whichever envelope the file uses."""
    if "results" in doc:
        return list(doc["results"])
    return [row for rep in doc.get("reports", []) for row in rep.get("results", [])]


def _mean(values):
    return sum(values) / len(values) if values else None


def summarise(doc):
    """One line's worth of numbers for a single result file, taken from speed_summary."""
    rows = _rows(doc)
    ok = speed_summary.completed(rows)
    if not ok:
        return None
    return {
        **speed_summary.summarise(rows),
        # Cut rows have no time to an answer, so the mean travels with its row count.
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
    """Result files in `directory`, skipping our `_`-prefixed ones, which have no `results`."""
    return sorted(
        f
        for f in glob.glob(os.path.join(directory, "*.json"))
        if not os.path.basename(f).startswith("_")
    )


def build_manifest(directory, title, model, generated):
    """Index every result file for the viewer, with a `kind` so each renders as itself."""
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
            # The viewer cannot import the probe's table, so older reports get kinds here.
            "correctness": correctness.annotate(doc.get("correctness")),
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
            # A row without integer passed/total is not a score ("/ = 0%").
            scored = [
                {
                    "label": r.get("label"),
                    "model": r.get("model"),
                    "passed": r.get("passed"),
                    "total": r.get("total"),
                    "effective_n": r.get("effective_n"),
                    # Observed passing cases: passed * n / total is wrong for uneven attempts.
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
    """The Hardware card's record: the first real `hardware` block, else a provenance one."""
    for which in (0, 1):
        for record in records:
            if isinstance(record[which], dict) and record[which]:
                return record[which]
    return {}


def run_fields(doc):
    """Per-run viewer fields beyond the rows: runtime, energy, threads, timestamp, speed."""
    provenance = doc.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    hardware = doc.get("hardware")
    if not isinstance(hardware, dict):
        hardware = {}
    return {
        "backend": doc.get("backend"),
        "model": doc.get("model"),
        # The runtime is this endpoint's only: a lanes report records its first lane's.
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
    """'benchmark' (shared envelope), 'throughput' (legacy shape) or 'unknown'."""
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
    """`report summary`: one run's figures under the speed runner's own names."""
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
