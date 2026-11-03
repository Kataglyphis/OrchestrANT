#!/usr/bin/env python3
"""Measure chat quality: does the model do what a chat user asked?

About thirty code-graded cases: instruction following, JSON-only replies,
multi-turn memory, and document QA at ~1k, ~3.5k and ~8k tokens.

Usage:
    python3 bench_chat.py --backend geniex-npu
    python3 bench_chat.py --compare candidates.json --repeats 3 --output chat.json
    python3 bench_chat.py --backend geniex-npu --category instruction --category json
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Standalone runs (not a package) need the repo root on sys.path for orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark import client as bench_cli
from orchestrant.benchmark.answers import accounting, from_body, split_answer
from orchestrant.benchmark.stats import format_score, tiers

from bench_chat_cases import CASES, DOC_SIZES, approx_tokens, document, grade

# determinism.py too: the probe this tool runs sets bench_compare's strict mode.
TOOL_FILES = (
    os.path.abspath(__file__),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_chat_cases.py"),
    "determinism.py",
)
# Room for a thinking model to leave <think> and answer.
DEFAULT_MAX_TOKENS = 2048

# ── the run ──────────────────────────────────────────────────────────────────

# bench_coding's overflow rule, copied: importing bench_coding needs the POSIX-only resource.
_OVERFLOW_BODY = re.compile(
    r"context|too (?:long|many tokens)|(?:context|prompt|input)[^.]{0,40}exceed"
    r"|max(?:imum)?_? ?(?:tokens|length)|(?:prompt|input|request) (?:is )?too",
    re.I,
)
_NOT_OVERFLOW_BODY = re.compile(r"rate.?limit|quota|billing", re.I)


def overflow_reason(exc):
    """The body of a 4xx refusing a prompt too long for the context, or None."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.code == 429:
        return None
    if not 400 <= exc.code < 500:
        return None
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    if _NOT_OVERFLOW_BODY.search(body) or not _OVERFLOW_BODY.search(body):
        return None
    return f"HTTP {exc.code}: {body[:160]}"


def ask(base_url, model, messages, max_tokens, entry=None, timeout=900):
    """One non-streamed chat request -> (response body, seconds)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    body.update(temperature=0, stream=False)
    url, started = f"{base_url}/v1/chat/completions", time.monotonic()
    with bench_cli.post_json(url, body, entry=entry, timeout=timeout) as r:
        data = r.json()
    if not isinstance(data, dict):
        raise TypeError(f"reply is not a JSON object: {str(data)[:80]}")
    return data, time.monotonic() - started


def case_messages(case):
    return case.get("messages") or [{"role": "user", "content": case["prompt"]}]


def grade_reply(case, body, max_tokens):
    """The graded fields of one reply: thinking stripped, a cut not graded."""
    reply = from_body(body)
    usage = reply.usage or {}
    completion = usage.get("completion_tokens")
    answer = split_answer(reply.content, reply.reasoning)[1].strip()
    blob = json.dumps([reply.content, reply.reasoning]).encode()
    fields = {
        "finish_reason": reply.finish_reason,
        "answered": accounting(reply, completion, max_tokens)["answered"],
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion,
        "reply_sha256": hashlib.sha256(blob).hexdigest(),
        "answer_preview": answer[:160],
    }
    cut = reply.finish_reason == "length" or (
        reply.finish_reason is None and bool(completion) and completion >= max_tokens
    )
    if cut:
        detail = f"CUT at {completion} tokens (budget {max_tokens}) - not graded"
        return dict(fields, truncated=True, detail=detail)
    passed, detail = grade(answer, case["checks"])
    return dict(fields, passed=passed, detail=detail)


def run_case(base_url, model, case, attempt, max_tokens, entry=None):
    """One attempt at one case -> its result row."""
    row = {"case": case["name"], "attempt": attempt, "category": case["category"]}
    row.update(dict.fromkeys(("passed", "errored", "overflow", "truncated"), False))
    row.update({"wall_s": None, "doc_tokens": case.get("doc_tokens")})
    try:
        body, wall = ask(base_url, model, case_messages(case), max_tokens, entry)
    except Exception as e:
        overflow = overflow_reason(e)
        if overflow:  # the model never saw the case: unmeasured, and named
            return dict(
                row, overflow=True, detail=f"OVERFLOW ({overflow}) - not graded"
            )
        # A transport failure is ours, not the model's: out of the rate.
        return dict(
            row, errored=True, detail=f"request failed: {type(e).__name__}: {e}"[:300]
        )
    return dict(row, **grade_reply(case, body, max_tokens), wall_s=round(wall, 2))


def measured(row):
    return not (row["errored"] or row["overflow"] or row["truncated"])


def _verdict(row):
    flags = (("errored", "ERROR"), ("overflow", "OVERFLOW"), ("truncated", "CUT"))
    word = next((word for flag, word in flags if row[flag]), None)
    return word or ("PASS" if row["passed"] else "FAIL")


def category_counts(results):
    """Per category: attempts passed/total, rows not graded, cases passed/cases."""
    cats, outcomes = {}, {}
    for r in results:
        c = cats.setdefault(r["category"], dict.fromkeys(("passed", "total", "excluded", "cases_passed", "cases"), 0))  # fmt: skip
        if not measured(r):
            c["excluded"] += 1
            continue
        c["total"] += 1
        c["passed"] += int(bool(r["passed"]))
        outcomes.setdefault((r["category"], r["case"]), set()).add(bool(r["passed"]))
    for (category, _), seen in outcomes.items():
        cats[category]["cases"] += 1
        cats[category]["cases_passed"] += int(seen == {True})
    return dict(sorted(cats.items()))


def summarise(results, repeats):
    """The report's counts; determinism from the reply hashes, as bench_tools."""
    rows = [r for r in results if measured(r)]
    passed = sum(1 for r in rows if r["passed"])
    hashes, outcomes = {}, {}
    for r in rows:
        hashes.setdefault(r["case"], set()).add(r["reply_sha256"])
        outcomes.setdefault(r["case"], set()).add(bool(r["passed"]))
    same = repeats > 1 and bool(hashes) and all(len(v) == 1 for v in hashes.values())
    agreed = (
        repeats > 1 and bool(outcomes) and all(len(v) == 1 for v in outcomes.values())
    )
    # Repeats that agree are one observation of the case, not `repeats` of them.
    n, k = len(rows), passed
    if same or agreed:
        n, k = len(outcomes), sum(1 for v in outcomes.values() if v == {True})
    walls = [r["wall_s"] for r in rows]
    counts = {
        f: sum(1 for r in results if r[f]) for f in ("errored", "overflow", "truncated")
    }
    return {
        "passed": passed,
        "total": len(rows),
        **counts,
        "deterministic": same,
        "repeats_agreed": agreed,
        "effective_n": n,
        "effective_k": k,
        "categories": category_counts(results),
        "total_wall_s": round(sum(walls), 2),
        "wall_measured_s": round(sum(walls), 2),
        "avg_wall_s": round(sum(walls) / len(walls), 2) if walls else None,
        "median_wall_s": round(statistics.median(walls), 2) if walls else None,
        "stdev_wall_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else None,
    }


_NOT_GRADED = (
    ("overflow", "OVERFLOW (did not fit the context)"),
    ("truncated", "CUT (no answer inside max_tokens)"),
    ("errored", "EXCLUDED (transport errors)"),
)


def print_summary(report):
    by_case = report["deterministic"] or report["repeats_agreed"]
    skipped = [f"{report[key]} {name}" for key, name in _NOT_GRADED if report[key]]
    score = format_score(report["effective_k"], report["effective_n"])
    line = f"    -> {score} {'cases' if by_case else 'attempts'}"
    line += f", not graded: {', '.join(skipped)}" if skipped else ""
    print(f"{line}, {report['total_wall_s']:.1f}s", flush=True)
    for name, c in report["categories"].items():
        k, n = (c["cases_passed"], c["cases"]) if by_case else (c["passed"], c["total"])
        extra = f"  (+{c['excluded']} not graded)" if c["excluded"] else ""
        print(f"       {name:12s} {format_score(k, n):>24s}{extra}", flush=True)
    if report["truncated"]:
        print(
            "       raise --max-tokens until nothing is CUT, then compare", flush=True
        )


def _print_row(row, suffix):
    wall = f"{row['wall_s']:6.2f}s" if row["wall_s"] is not None else "      -"
    detail = "" if row["passed"] else row.get("detail", "")[:70]
    print(
        f"    {row['case']:26s}{suffix} {_verdict(row):8s} {wall}  {detail}", flush=True
    )


def evaluate(
    base_url,
    model,
    label,
    repeats=1,
    max_tokens=DEFAULT_MAX_TOKENS,
    warmup=True,
    entry=None,
    backend=None,
    cases=None,
):
    """Every case against one endpoint -> one report row; `backend` lets a ranking find the control."""
    print(f"\n  === {label} ===", flush=True)
    if warmup:  # otherwise the first case carries the model's load time
        try:
            ready = case_messages({"prompt": "Reply with the single word: ready."})
            ask(base_url, model, ready, 16, entry)
        except Exception as e:
            print(f"    (warmup failed: {type(e).__name__})", flush=True)
    results = []
    for case in CASES if cases is None else cases:
        for attempt in range(repeats):
            if attempt:
                # Never an identical follow-up (see client.spacer).
                bench_cli.spacer(base_url, model, entry)
            results.append(run_case(base_url, model, case, attempt, max_tokens, entry))
            _print_row(
                results[-1], f" [{attempt + 1}/{repeats}]" if repeats > 1 else ""
            )
    report = {
        "label": label,
        "model": model,
        "backend": backend,
        "max_tokens": max_tokens,
    }
    report.update(summarise(results, repeats), results=results)
    print_summary(report)
    return report


def print_ranking(reports, suspect):
    """Rate over measured cases, then coverage, then time; ties by the sign test."""
    from bench_compare import case_outcomes, is_control

    print("\n" + "=" * 70 + "\n  RANKING — chat cases passed, then time\n" + "=" * 70)
    if suspect:
        print(f"  SUSPECT (the control fails them too, excluded): {', '.join(suspect)}")
    contenders = [r for r in reports if not is_control(r)] or reports
    ranked = sorted(
        contenders,
        key=lambda x: (
            -x["passed"] / (x["total"] or 1),
            -x["total"],
            x["total_wall_s"],
        ),
    )
    tiered = tiers(ranked, case_outcomes)
    for i, group in enumerate(tiered):
        if i:
            print(f"  {'-' * 40} tier {i + 1} " + "-" * 20)
        for r in group:
            score = format_score(r["effective_k"], r["effective_n"])
            print(f"  {r['label'][:40]:40s} {score:>24s} {r['total_wall_s']:8.1f}s")
    if len(tiered) < len(ranked):
        print(
            "  rows within one tier are NOT separated by the paired sign test: a tie."
        )


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    for flag in ("--backend", "--base-url", "--model", "--label", "--output"):
        ap.add_argument(flag, default=None)
    ap.add_argument("--compare", default=None, help="a candidates JSON file")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="per reply; a reply cut before its answer finished is CUT, not graded",
    )
    ap.add_argument(
        "--category",
        action="append",
        choices=sorted({c["category"] for c in CASES}),
        help="run only this category (repeatable); the report records which ran",
    )
    ap.add_argument("--no-warmup", action="store_true")
    return ap.parse_args(argv)


def _write(args, candidates, reports, cases, start, tool_files):
    # One copy of the determinism probe for the lab: bench_tools' already feeds provenance.
    from bench_tools import _determinism_extra

    extra = _determinism_extra(candidates)
    sizes = {label: approx_tokens(document(n)) for n, label in DOC_SIZES.items()}
    config = {
        "repeats": args.repeats,
        # Repeats of one case are separated by a throwaway request.
        "repeat_spacer": True,
        "warmup": not args.no_warmup,
        "max_tokens": args.max_tokens,
        "categories": sorted({c["category"] for c in cases}),
        "cases": len(cases),
        "doc_tokens_estimated": sizes,
        "backend_entry": {
            c["label"]: bench_cli.entry_config(c["entry"]) for c in candidates
        },
    }
    base_url = candidates[0]["base_url"] if candidates else None
    # `start` (run_start) names a source edited mid-run and records the load.
    bench_cli.write_report(
        args.output,
        "bench_chat",
        config,
        reports,
        base_url,
        tool_files,
        extra=extra,
        run_start=start,
    )
    print(f"  Report written to {args.output}")


def main(argv=None):
    bench_cli.utf8_stdio()
    args = parse_args(argv)
    from compare_suspect import is_control, mark_suspect_cases, suspect_tool_files
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    cases = [c for c in CASES if not args.category or c["category"] in args.category]
    candidates = bench_cli.candidate_rows(args, resolve_backend, resolve_backend_entry)
    tool_files = TOOL_FILES + suspect_tool_files(candidates)
    start = bench_cli.run_start(
        tool_files, candidates[0]["base_url"] if candidates else None
    )
    reports = [
        evaluate(
            c["base_url"],
            c["model"],
            c["label"],
            repeats=args.repeats,
            max_tokens=args.max_tokens,
            warmup=not args.no_warmup,
            entry=c["entry"],
            backend=c["backend"],
            cases=cases,
        )
        for c in candidates
    ]
    # A case the CONTROL endpoint also fails is evidence about the case.
    suspect = mark_suspect_cases(reports)
    for report in (r for r in reports if suspect and not is_control(r)):
        # That re-derives a bare passed/total; this tool's categories also count ungraded rows.
        kept = [r for r in report["results"] if not r.get("suspect")]
        report["categories"] = category_counts(kept)
    if args.output:
        _write(args, candidates, reports, cases, start, tool_files)
    # Ranking last: it only prints, and must never cost a written report.
    if len(reports) > 1:
        print_ranking(reports, suspect)
    return 0


if __name__ == "__main__":
    sys.exit(main())
