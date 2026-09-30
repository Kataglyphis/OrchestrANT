#!/usr/bin/env python3
"""Run the whole benchmark suite over a candidates file, in one command.

The correctness gate runs first for every candidate, and no output file is
ever overwritten.

Usage:
    python3 bench_sweep.py --candidates candidates.json --outdir results/run1 \\
        --tools speed,coding,tools --repeats 3
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))

# Subprocesses need the repo root (one level up) on PYTHONPATH, not just on sys.path.
REPO_ROOT = os.path.dirname(HERE)
os.environ["PYTHONPATH"] = REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")

# 'agent' drives opencode, which takes its endpoint from opencode.jsonc, not --backend.
TOOLS = ("speed", "coding", "tools", "chat", "agent", "lanes")


def slug(label):
    """A filename fragment from a label. Never empty, never a path."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(label)).strip("-").lower()
    return text or "unnamed"


def output_path(outdir, tool, label):
    return os.path.join(outdir, f"{tool}_{slug(label)}.json")


def plan(candidates, tools, outdir):
    """[(tool, candidate, path)] for the whole sweep; labels that slug alike are refused."""
    steps, seen = [], {}
    for cand in candidates:
        for tool in tools:
            path = output_path(outdir, tool, cand["label"])
            if path in seen:
                raise SystemExit(
                    f"{cand['label']!r} and {seen[path]!r} both write {path}. "
                    f"Give them labels that differ by more than punctuation."
                )
            if os.path.exists(path):
                raise SystemExit(
                    f"{path} already exists. A sweep never overwrites a "
                    f"measurement: use a fresh --outdir, or delete it yourself."
                )
            seen[path] = cand["label"]
            steps.append((tool, cand, path))
    return steps


def _labelled(script, endpoint, cand, path, args):
    """The argv of a tool taking the candidate's label, the sweep's repeats and the output."""
    return (
        [sys.executable, os.path.join(HERE, script)]
        + endpoint
        + ["--label", cand["label"], "--repeats", str(args.repeats), "--output", path]
    )


def tool_command(tool, cand, path, args):
    """The argv for one tool run. Pure: the tests read it without running it."""
    py = sys.executable
    backend = ["--backend", cand["backend"]] if cand.get("backend") else []
    # Backend plus URL override: the URL wins, the backend still supplies headers and keys.
    if cand.get("base_url") and (not backend or cand.get("raw_base_url")):
        backend += ["--base-url", cand["base_url"]]
    model = ["--model", cand["model"]] if cand.get("model") else []

    if tool == "speed":
        # --label is not a flag orchestrant.benchmark.openai_api has; the file name carries it.
        return (
            [py, "-m", "orchestrant.benchmark", "speed"]
            + backend
            + model
            + ["--stream", "--prompts", "8", "--output", path]
        )
    if tool == "coding":
        return (
            [py, os.path.join(HERE, "bench_coding.py")]
            + backend
            + model
            + [
                "--label",
                cand["label"],
                "--task-set",
                args.task_set,
                "--repeats",
                str(args.repeats),
                "--keep-output",
                "--output",
                path,
            ]
        )
    if tool == "tools":
        return _labelled("bench_tools.py", backend + model, cand, path, args)
    if tool == "chat":
        # Its own --max-tokens and every category: chat_<label>.json is the whole instrument.
        return _labelled("bench_chat.py", backend + model, cand, path, args)
    if tool == "agent":
        # opencode picks its own endpoint out of opencode.jsonc.
        return _labelled("bench_agent.py", model, cand, path, args)
    if tool == "lanes":
        return (
            [py, "-m", "orchestrant.benchmark", "lanes"]
            + backend
            + model
            + ["--batching", "--output", path]
        )
    raise SystemExit(f"unknown tool {tool!r}. Known: {', '.join(TOOLS)}")


def run_step(cmd):
    """Run one tool; the seam every test monkeypatches."""
    print(f"    $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=HERE, check=False).returncode


def gate(cand, max_tokens=4000):
    """The correctness gate for one candidate, before any measurement.

    Returns the probe dict plus a verdict from its integrity items: unreachable,
    wrong, truncated or ok.
    """
    from orchestrant.benchmark import correctness
    from orchestrant.benchmark.openai_api import run_correctness_probe

    probe = run_correctness_probe(
        cand["model"],
        max_tokens=max_tokens,
        base_url=cand["base_url"],
        entry=cand.get("entry"),
    )
    kinds = correctness.by_kind(probe) or {}
    verdict = {
        correctness.NO_RESULT: "unreachable",
        "OK": "ok",
        "INCONCLUSIVE": "truncated",
    }.get(correctness.verdict(kinds.get(correctness.INTEGRITY)), "wrong")
    if probe is None:
        return {"verdict": verdict, "score": None, "total": None}
    return {
        "verdict": verdict,
        "score": probe.get("score"),
        "total": probe.get("total"),
        "wrong": probe.get("wrong"),
        "truncated": probe.get("truncated"),
        "errors": probe.get("errors"),
        **kinds,
    }


def sweep(candidates, args):
    """Gate every candidate, run every tool, then the manifest; return the _sweep.json summary."""
    steps = plan(candidates, args.tools, args.outdir)
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "outdir": args.outdir,
        "tools": list(args.tools),
        "repeats": args.repeats,
        "task_set": args.task_set,
        "candidates": [],
        "steps": [],
    }

    gates = {}
    for cand in candidates:
        if args.skip_gate:
            verdict = {"verdict": "skipped", "score": None, "total": None}
        else:
            print(f"\n  ▸ correctness gate: {cand['label']}", flush=True)
            try:
                verdict = gate(cand)
            except SystemExit:
                raise  # a missing API key names the variable; do not bury it
            except Exception as e:  # noqa: BLE001 — a dead lane is a verdict
                verdict = {
                    "verdict": "unreachable",
                    "error": str(e)[:200],
                    "score": None,
                    "total": None,
                }
        gates[cand["label"]] = verdict
        print(
            f"    gate: {verdict['verdict']}"
            + (
                f" ({verdict['score']}/{verdict['total']})"
                if verdict.get("total")
                else ""
            ),
            flush=True,
        )
        summary["candidates"].append(
            {
                "label": cand["label"],
                "backend": cand.get("backend"),
                "base_url": cand["base_url"],
                "model": cand["model"],
                "gate": verdict,
            }
        )

    for tool, cand, path in steps:
        label = cand["label"]
        if gates[label]["verdict"] == "unreachable":
            # An endpoint the probe could not reach would yield numbers about nothing.
            print(f"  -- {tool} / {label}: skipped, gate says unreachable", flush=True)
            summary["steps"].append(
                {
                    "tool": tool,
                    "label": label,
                    "output": path,
                    "argv": tool_command(tool, cand, path, args),
                    "status": "skipped-gate",
                    "returncode": None,
                }
            )
            continue
        print(f"\n  ▸ {tool}: {label}", flush=True)
        # Stored, not just printed: an audit reads the exact command from _sweep.json.
        cmd = tool_command(tool, cand, path, args)
        rc = run_step(cmd)
        summary["steps"].append(
            {
                "tool": tool,
                "label": label,
                "output": path,
                "argv": cmd,
                "returncode": rc,
                "status": "ok" if rc == 0 else "failed",
                "written": os.path.exists(path),
            }
        )
        if rc != 0:
            print(
                f"    {tool} exited {rc} — continuing with the rest of the sweep",
                flush=True,
            )

    written = [s["output"] for s in summary["steps"] if s.get("written")]

    manifest = os.path.join(args.outdir, "_manifest.json")
    manifest_cmd = [
        sys.executable,
        "-m",
        "orchestrant.benchmark",
        "report",
        "manifest",
        args.outdir,
        manifest,
        "--title",
        args.title or f"Sweep {args.outdir}",
        "--model",
        ", ".join(sorted({c["model"] or "?" for c in candidates})),
        "--generated",
        summary["generated"],
    ]
    summary["manifest"] = {
        "path": manifest,
        "argv": manifest_cmd,
        "returncode": run_step(manifest_cmd),
    }

    if args.baseline:
        from compare_verdict import CONDITIONS_DIFFER  # imports the package

        summary["compare"] = []
        for path in written:
            cmd = [
                sys.executable,
                os.path.join(HERE, "bench_compare.py"),
                "--baseline",
                args.baseline,
                path,
            ]
            rc = run_step(cmd)
            # Advisory, as in run_benchmarks.sh: exit 1 regressed, 4 withheld; recorded either way.
            summary["compare"].append(
                {
                    "report": path,
                    "argv": cmd,
                    "returncode": rc,
                    "regressed": rc == 1,
                    "conditions_differ": rc == CONDITIONS_DIFFER,
                }
            )

    out = os.path.join(args.outdir, "_sweep.json")
    tmp = f"{out}.tmp"
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=2)
    os.replace(tmp, out)
    print(f"\n  Sweep summary written to {out}", flush=True)
    return summary


def parse_tools(value):
    tools = [t.strip() for t in value.split(",") if t.strip()]
    unknown = [t for t in tools if t not in TOOLS]
    if unknown:
        raise SystemExit(f"unknown tool(s) {unknown}. Known: {', '.join(TOOLS)}")
    if not tools:
        raise SystemExit("--tools needs at least one of: " + ", ".join(TOOLS))
    return tools


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--candidates",
        required=True,
        help="JSON list of candidates (see candidates.example.json)",
    )
    ap.add_argument(
        "--outdir",
        required=True,
        help="Directory for the derived <tool>_<label>.json reports",
    )
    ap.add_argument(
        "--tools",
        default="speed,coding,tools",
        help="Comma-separated: " + ", ".join(TOOLS),
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Passed to bench_coding/bench_tools/bench_chat/bench_agent (default 1)",
    )
    ap.add_argument(
        "--task-set", default="all", help="Passed to bench_coding (default all)"
    )
    ap.add_argument(
        "--baseline",
        default=None,
        help="Stored bench_compare baseline name to compare every "
        "written report against",
    )
    ap.add_argument("--title", default=None, help="Manifest title")
    ap.add_argument(
        "--skip-gate",
        action="store_true",
        help="Do not run the correctness probe first. Only for a "
        "paid endpoint you have already verified — an "
        "ungated sweep can measure a broken lane for hours.",
    )
    args = ap.parse_args(argv)
    if args.repeats < 1:  # bench_agent refuses it, the others measure nothing
        ap.error(f"--repeats must be at least 1, got {args.repeats}")
    args.tools = parse_tools(args.tools)

    from orchestrant.benchmark.client import load_candidates
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    candidates = load_candidates(
        args.candidates, resolve_backend, resolve_backend_entry
    )
    if not candidates:
        raise SystemExit(f"{args.candidates}: no candidates in the file")

    if args.baseline:
        from bench_compare import baseline_path

        if not os.path.exists(baseline_path(args.baseline)):
            raise SystemExit(
                f"no baseline named {args.baseline!r}. Record one with "
                f"bench_compare.py --save-baseline {args.baseline} <report>"
            )

    os.makedirs(args.outdir, exist_ok=True)
    summary = sweep(candidates, args)
    # Exit 0 after measuring nothing would be an assertion-free PASS.
    if summary["steps"] and all(
        s["status"] == "skipped-gate" for s in summary["steps"]
    ):
        raise SystemExit(
            "every candidate was skipped by the correctness gate — nothing was measured"
        )
    failed = [s for s in summary["steps"] if s["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    # A cp1252 redirect raises on "▸"; inline, as this driver does not import the package.
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
