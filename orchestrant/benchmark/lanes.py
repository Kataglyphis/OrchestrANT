#!/usr/bin/env python3
"""Concurrency benchmarks across one or more serving endpoints (LB4 + LB5).

Two questions the single-endpoint sweep cannot answer:

LB5 --batching: does ONE server overlap concurrent requests?
    Fire two requests at the same endpoint simultaneously. If the second one's
    first token arrives only after the first request has fully finished, the
    server serialises and has no continuous batching -- so extra throughput
    must come from more servers, not more clients. (Measured on GenieX: the
    second request waited 27.6 s, exactly the duration of the first, and the
    server would not even answer /v1/models meanwhile.)

LB4 --lanes: do several servers ADD UP, or fight each other?
    Drive N endpoints at once and report per-lane plus aggregate throughput.
    Compute units differ wildly here: on one Snapdragon host the NPU and GPU
    lanes cost each other ~1-3 % (19.25 + 12.11 = 31.4 tok/s) while a CPU lane
    and a GPU lane contend for the same cores. None of that is derivable from
    sequential single-endpoint runs.

Usage:
    # does this server batch?
    python3 bench_lanes.py --batching --backend ollama

    # do these lanes add up?  (names come from backends.json)
    python3 bench_lanes.py --lanes geniex-npu geniex-cpu

    # ...or spell an endpoint out in full
    python3 bench_lanes.py --lanes \
        npu=http://127.0.0.1:18181,model=qualcomm/Qwen3-4B-Instruct-2507:W4A16
"""

import argparse
import json
import threading
import time
import urllib.request


DEFAULT_PROMPT = (
    "Write a Python function that merges two sorted lists. Explain briefly."
)


def stream_once(base_url, model, prompt, max_tokens=256, timeout=900, deadline=900):
    """One streaming request. Returns timing dict (never raises).

    `timeout` is urlopen's and applies PER SOCKET READ, so a model that keeps
    emitting tokens never trips it -- one blocked a bench_coding sweep for over
    an hour on 2026-09-05. `deadline` bounds the whole request; what arrived by
    then is returned with `gave_up`. The 256-token default makes this unlikely
    here, but a lane sweep exists to measure lanes, not to hang on one.
    """
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    started = time.monotonic()
    ttft = None
    tokens = 0
    gave_up = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if deadline and time.monotonic() - started > deadline:
                    gave_up = True
                    break
                line = raw.decode("utf-8", "replace").strip()
                # The space after "data:" is optional per the SSE spec.
                if not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices and (choices[0].get("delta", {}).get("content")):
                    if ttft is None:
                        ttft = time.monotonic() - started
                    tokens += 1
    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {e}",
            "wall_s": time.monotonic() - started,
        }

    wall = time.monotonic() - started
    decode_window = wall - (ttft or 0)
    return {
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "gave_up": gave_up,
        "tokens": tokens,
        "wall_s": round(wall, 2),
        "decode_tok_per_sec": round((tokens - 1) / decode_window, 2)
        if decode_window > 0 and tokens > 1
        else 0.0,
    }


def _secs(value, width=6):
    """Format a possibly-missing duration.

    A request can succeed and return NOTHING — that is exactly what the QAIRT
    lane does past its context limit: HTTP 200, zero tokens, ttft_s None. The
    print statements used to raise TypeError on it and take the whole lane run
    with them.
    """
    return (
        f"{value:{width}.2f}"
        if isinstance(value, (int, float))
        else f"{'n/a':>{width}}"
    )


def run_parallel(jobs):
    """Run (label, fn) jobs at the same time; return {label: result}."""
    out = {}
    lock = threading.Lock()

    def work(label, fn):
        result = fn()
        with lock:
            out[label] = result

    threads = [threading.Thread(target=work, args=(lbl, fn)) for lbl, fn in jobs]
    wall_start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out, time.monotonic() - wall_start


def probe_batching(base_url, model, prompt, max_tokens):
    """LB5 — decide whether one endpoint overlaps two concurrent requests."""
    print(f"\n  Batching probe: two concurrent requests to {base_url}")
    results, wall = run_parallel(
        [
            (f"req{i}", (lambda: stream_once(base_url, model, prompt, max_tokens)))
            for i in range(2)
        ]
    )

    bad = [k for k, v in results.items() if "error" in v]
    if bad:
        for k in bad:
            print(f"    {k}: ERROR {results[k]['error']}")
        return None

    if any(r.get("ttft_s") is None for r in results.values()):
        print(
            "    VERDICT: inconclusive — a request returned no tokens at all "
            "(past a context limit?), so there is no first-token time to compare"
        )
        return {
            "verdict": "inconclusive",
            "serialised": None,
            "wall_s": round(wall, 2),
            "requests": results,
        }
    ordered = sorted(results.values(), key=lambda r: r["ttft_s"])
    first, second = ordered[0], ordered[1]
    for name, r in sorted(results.items()):
        print(
            f"    {name}: ttft={_secs(r['ttft_s'])}s  "
            f"{_secs(r['decode_tok_per_sec'])} tok/s  wall={_secs(r['wall_s'])}s"
        )

    # If the later request only started producing after the earlier one had
    # essentially finished, the server ran them one after another.
    serialised = (second["ttft_s"] or 0) >= first["wall_s"] * 0.8
    verdict = "SERIALISED (no batching)" if serialised else "OVERLAPPED (batching)"
    print(f"    wall for both: {wall:.2f}s")
    print(f"    VERDICT: {verdict}")
    if serialised:
        print("    -> extra throughput needs MORE SERVERS, not more clients.")
    return {
        "verdict": verdict,
        "serialised": serialised,
        "wall_s": round(wall, 2),
        "requests": results,
    }


def run_lanes(lanes, prompt, max_tokens, sequential_baseline=True):
    """LB4 — per-lane and aggregate throughput when lanes run together."""
    report = {"lanes": {}, "baseline": {}}

    if sequential_baseline:
        print("\n  Baseline — each lane alone:")
        for name, (url, model) in lanes.items():
            r = stream_once(url, model, prompt, max_tokens)
            report["baseline"][name] = r
            if "error" in r:
                print(f"    {name:10s} ERROR {r['error']}")
            else:
                print(
                    f"    {name:10s} {_secs(r['decode_tok_per_sec'])} tok/s   "
                    f"ttft={_secs(r['ttft_s'])}s"
                )

    print("\n  Together — all lanes at once:")
    results, wall = run_parallel(
        [
            (name, (lambda u=url, m=model: stream_once(u, m, prompt, max_tokens)))
            for name, (url, model) in lanes.items()
        ]
    )
    report["lanes"] = results

    total = 0.0
    for name in lanes:
        r = results[name]
        if "error" in r:
            print(f"    {name:10s} ERROR {r['error']}")
            continue
        total += r["decode_tok_per_sec"]
        base = report["baseline"].get(name, {}).get("decode_tok_per_sec")
        delta = ""
        if base:
            change = 100 * (r["decode_tok_per_sec"] - base) / base
            delta = f"   ({change:+.0f}% vs alone)"
        print(
            f"    {name:10s} {_secs(r['decode_tok_per_sec'])} tok/s   "
            f"ttft={_secs(r['ttft_s'])}s{delta}"
        )

    best_alone = max(
        (v.get("decode_tok_per_sec", 0) for v in report["baseline"].values()), default=0
    )
    print(
        f"\n    AGGREGATE: {total:.1f} tok/s across {len(lanes)} lanes "
        f"(wall {wall:.1f}s)"
    )
    if best_alone:
        print(
            f"    Best single lane alone: {best_alone:.1f} tok/s "
            f"-> {total / best_alone:.2f}x by running lanes together"
        )
    print(
        "    NOTE: aggregate only materialises with that many CONCURRENT "
        "requests.\n          One agent waiting for one answer still sees a "
        "single lane's speed."
    )
    report["aggregate_tok_per_sec"] = round(total, 2)
    report["wall_s"] = round(wall, 2)
    return report


def sweep_nctx(base_url_template, model, values, prompt, max_tokens=256):
    """Does --nctx change anything but memory?

    Swept 2026-09-01 (docs/geniex-local-ai-setup.md 1h): a larger context
    window costs nothing measurable, so 16384 stands. No CLI flag reaches this;
    call it from a script when a new lane needs the same question answered.
    """
    print("\n  nctx sweep (restart the lane between values):", flush=True)
    rows = []
    for value in values:
        r = stream_once(base_url_template, model, prompt, max_tokens)
        if "error" in r:
            print(f"    nctx={value:6d}  ERROR {r['error'][:50]}", flush=True)
            rows.append({"nctx": value, "error": r["error"][:120]})
            continue
        print(
            f"    nctx={value:6d}  {r['decode_tok_per_sec']:6.2f} tok/s  "
            f"ttft={_secs(r['ttft_s'])}s",
            flush=True,
        )
        rows.append({"nctx": value, **r})
    return rows


def parse_lane(spec):
    """Parse 'name=URL,model=MODEL' into (name, url, model)."""
    try:
        head, model_part = spec.split(",model=", 1)
        name, url = head.split("=", 1)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"bad lane {spec!r}; expected name=URL,model=MODEL"
        )
    return name.strip(), url.strip().rstrip("/"), model_part.strip()


def resolve_lane(spec, path=None):
    """Accept either a full 'name=URL,model=MODEL' spec or a bare backend name.

    `path` selects the registry file. Without it the unit tests were wired to
    the shipped backends.json and broke whenever anyone edited it — a test that
    fails for an unrelated edit teaches people to ignore the suite.

    Naming a backend is the common case -- `--lanes geniex-npu geniex-cpu`
    reads far better than two URLs, and keeps the endpoints in one place
    (backends.json) instead of scattered across shell history.
    """
    if "=" in spec:
        return parse_lane(spec)

    from orchestrant.benchmark.openai_api import load_backends

    backends, _ = load_backends(path)
    if spec not in backends:
        known = ", ".join(sorted(backends)) or "(none configured)"
        raise argparse.ArgumentTypeError(
            f"unknown backend {spec!r}. Known: {known}. "
            "Or give a full spec: name=URL,model=MODEL"
        )
    entry = backends[spec]
    model = entry.get("model")
    if not model:
        raise argparse.ArgumentTypeError(
            f"backend {spec!r} has no default model in backends.json; "
            f"use {spec}={entry['base_url']},model=<model> instead"
        )
    return spec, entry["base_url"].rstrip("/"), model


def build_reports(batching=None, batching_endpoint=None, lane_run=None, lanes=None):
    """Shape the probes' output as the shared envelope's reports[] rows.

    One row per lane plus an 'aggregate' row (both carry `tok_per_sec`, which
    bench_compare diffs with a tolerance), and a 'batching' row carrying the
    verdict. None of them has passed/total: these are not scores.
    """
    reports = []
    if batching_endpoint is not None:
        url, model = batching_endpoint
        row = {"label": "batching", "model": model, "base_url": url}
        row.update(batching or {"verdict": "error", "serialised": None, "requests": {}})
        reports.append(row)
    if lane_run is not None:
        for name, (url, model) in (lanes or {}).items():
            together = lane_run["lanes"].get(name, {})
            reports.append(
                {
                    "label": name,
                    "model": model,
                    "base_url": url,
                    "alone": lane_run["baseline"].get(name),
                    "together": together,
                    "tok_per_sec": together.get("decode_tok_per_sec"),
                }
            )
        reports.append(
            {
                "label": "aggregate",
                "model": None,
                "base_url": None,
                "lanes": sorted(lanes or {}),
                "tok_per_sec": lane_run["aggregate_tok_per_sec"],
                "wall_s": lane_run["wall_s"],
            }
        )
    return reports


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--batching",
        action="store_true",
        help="LB5: does one endpoint overlap concurrent requests?",
    )
    ap.add_argument(
        "--lanes",
        nargs="+",
        metavar="BACKEND|name=URL,model=MODEL",
        help="LB4: drive these endpoints simultaneously. Either a "
        "backend name from backends.json (e.g. geniex-npu) or "
        "a full name=URL,model=MODEL spec.",
    )
    ap.add_argument(
        "--base-url",
        "--endpoint",
        dest="base_url",
        default=None,
        help="Endpoint URL for --batching (overrides --backend). "
        "--endpoint is kept as an alias: it was the original "
        "spelling here while both sibling tools used "
        "--base-url, and an operator scripting the suite had "
        "to remember which tool wanted which.",
    )
    ap.add_argument(
        "--backend",
        default=None,
        help="Named backend from backends.json for --batching",
    )
    ap.add_argument("--model", default=None, help="Model for --batching")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip the one-lane-at-a-time baseline",
    )
    ap.add_argument("--output", default=None, help="Write the report as JSON")
    args = ap.parse_args()

    if not args.batching and not args.lanes:
        ap.error("nothing to do: pass --batching and/or --lanes")

    batching = endpoint = lane_run = lanes = None

    if args.batching:
        from orchestrant.benchmark.openai_api import (
            detect_model_via_api,
            resolve_backend,
        )

        url, backend_model, source = resolve_backend(args.backend, args.base_url)
        model = args.model or backend_model or detect_model_via_api(url)
        print(f"  Endpoint: {url}  (from {source})")
        endpoint = (url, model)
        batching = probe_batching(url, model, args.prompt, args.max_tokens)

    if args.lanes:
        lanes = {}
        for spec in args.lanes:
            name, url, model = resolve_lane(spec)
            lanes[name] = (url, model)
        lane_run = run_lanes(
            lanes,
            args.prompt,
            args.max_tokens,
            sequential_baseline=not args.no_baseline,
        )

    print()
    if args.output:
        from orchestrant.benchmark.client import write_report

        # The shared envelope, so bench_report labels it and bench_compare can
        # diff it; the bare dict passed "no regression" against anything.
        base_url = endpoint[0] if endpoint else next(iter(lanes.values()))[0]
        write_report(
            args.output,
            "bench_lanes",
            {
                "prompt": args.prompt,
                "max_tokens": args.max_tokens,
                "baseline": not args.no_baseline,
            },
            build_reports(batching, endpoint, lane_run, lanes),
            base_url,
            ("lanes.py", "provenance.py"),
        )
        print(f"  Report written to {args.output}")


if __name__ == "__main__":
    main()
