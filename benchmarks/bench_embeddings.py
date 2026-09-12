#!/usr/bin/env python3
"""Measure an embedding endpoint — speed AND whether the vectors mean anything.

tests/test_v1_api.py exercises the embedding endpoints and nothing measures
them. That matters the moment a RAG or code-search path exists: an endpoint can
return well-formed vectors of the right dimension at a fine rate and still be
useless, because the numbers carry no semantic structure.

So this checks three things, in increasing order of what can go wrong:

  1. shape — right dimension, finite numbers, stable across calls;
  2. speed — texts per second and per-text latency, by input size;
  3. MEANING — do related texts land closer together than unrelated ones?
     This is the check a shape test cannot make, and the one that catches a
     broken quantisation or a mis-wired pooling layer. A model that fails it
     will happily power a search feature that returns nonsense.

Usage:
    python3 bench_embeddings.py --backend ollama --model nomic-embed-text
"""

import argparse
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the runner lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark.client import entry_config, post_json  # noqa: E402

# Triples: (anchor, related, unrelated). The related text must be closer to the
# anchor than the unrelated one. Chosen so the judgement is not arguable — an
# ambiguous triple would measure the author's taste, not the model.
TRIPLES = [
    (
        "How do I open a file in Python?",
        "Reading a text file with Python's open() function",
        "The migratory patterns of Arctic terns",
    ),
    (
        "The server returned a 500 error",
        "An internal server error occurred while handling the request",
        "A recipe for sourdough bread starter",
    ),
    (
        "git commit --amend rewrites the last commit",
        "Amending the most recent commit in git",
        "Photosynthesis converts light into chemical energy",
    ),
    (
        "The cat sat on the mat",
        "A cat was sitting on a rug",
        "Quarterly revenue exceeded analyst expectations",
    ),
    (
        "def merge_sorted(a, b): merge two sorted lists",
        "A function that combines two ordered sequences into one",
        "The Treaty of Westphalia ended the Thirty Years' War",
    ),
]

SIZES = [
    ("short", "hello world"),
    ("medium", "The quick brown fox jumps over the lazy dog. " * 10),
    ("long", "The quick brown fox jumps over the lazy dog. " * 100),
]


def embed(base_url, model, texts, timeout=300, entry=None):
    started = time.monotonic()
    with post_json(
        f"{base_url}/v1/embeddings",
        {"model": model, "input": texts},
        entry=entry,
        timeout=timeout,
    ) as r:
        data = r.json()
    wall = time.monotonic() - started
    vectors = [
        item["embedding"]
        for item in sorted(data["data"], key=lambda d: d.get("index", 0))
    ]
    return vectors, wall


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def check_shape(vectors):
    """Right dimension, finite numbers, non-degenerate."""
    problems = []
    if not vectors:
        return ["no vectors returned"]
    dims = {len(v) for v in vectors}
    if len(dims) != 1:
        problems.append(f"inconsistent dimensions: {sorted(dims)}")
    for i, v in enumerate(vectors):
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v):
            problems.append(f"vector {i} contains non-finite values")
            break
        if all(x == 0 for x in v):
            problems.append(f"vector {i} is all zeros")
            break
    return problems


def run(base_url, model, label, entry=None):
    print(f"\n  === {label} ===", flush=True)
    report = {"label": label, "model": model, "base_url": base_url}

    # --- shape and determinism
    try:
        first, _ = embed(base_url, model, ["shape probe"], entry=entry)
        second, _ = embed(base_url, model, ["shape probe"], entry=entry)
    except Exception as e:  # noqa: BLE001
        print(f"    endpoint unusable: {type(e).__name__}: {e}", flush=True)
        return {**report, "error": str(e)[:200]}

    problems = check_shape(first)
    identical = first and second and cosine(first[0], second[0]) > 0.9999
    report["dimension"] = len(first[0]) if first else None
    report["shape_problems"] = problems
    report["deterministic"] = bool(identical)
    print(
        f"    dimension {report['dimension']}, "
        f"{'deterministic' if identical else 'NOT deterministic'}"
        + (f", problems: {problems}" if problems else ""),
        flush=True,
    )

    # --- speed by input size
    speed = {}
    for name, text in SIZES:
        try:
            _, wall = embed(base_url, model, [text], entry=entry)
            speed[name] = round(wall, 3)
            print(f"    {name:7s} ({len(text):5d} chars): {wall:6.3f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            speed[name] = None
            print(f"    {name:7s}: ERROR {type(e).__name__}", flush=True)
    report["latency_s"] = speed

    # --- batching: does the endpoint actually batch, or loop internally?
    try:
        _, one = embed(base_url, model, ["batch probe"], entry=entry)
        _, eight = embed(base_url, model, ["batch probe"] * 8, entry=entry)
        report["batch_speedup"] = round((one * 8) / eight, 2) if eight else None
        print(
            f"    batch of 8 vs 8 singles: {report['batch_speedup']}x "
            f"({'batches' if (report['batch_speedup'] or 0) > 2 else 'little or no batching'})",
            flush=True,
        )
    except Exception:  # noqa: BLE001
        report["batch_speedup"] = None

    # --- MEANING: the check a shape test cannot make
    passed, margins = 0, []
    for anchor, related, unrelated in TRIPLES:
        try:
            vecs, _ = embed(base_url, model, [anchor, related, unrelated], entry=entry)
            near = cosine(vecs[0], vecs[1])
            far = cosine(vecs[0], vecs[2])
            ok = near > far
            passed += ok
            margins.append(round(near - far, 4))
            print(
                f"    {'ok ' if ok else 'FAIL'}  related {near:+.3f} vs "
                f"unrelated {far:+.3f}  (margin {near - far:+.3f})",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"    ERROR on a triple: {type(e).__name__}", flush=True)
    report["semantic_passed"] = passed
    report["semantic_total"] = len(TRIPLES)
    report["margins"] = margins
    report["median_margin"] = round(statistics.median(margins), 4) if margins else None
    print(
        f"    -> semantics {passed}/{len(TRIPLES)}"
        + (f", median margin {report['median_margin']:+.3f}" if margins else ""),
        flush=True,
    )
    if passed < len(TRIPLES):
        print(
            "       NOTE: a model that cannot separate related from unrelated "
            "text will\n       happily power a search feature that returns "
            "nonsense. Shape alone\n       would have passed it.",
            flush=True,
        )
    return report


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--backend", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--compare", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    from orchestrant.benchmark.client import resolve_candidates, write_report
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    candidates = resolve_candidates(args, resolve_backend, resolve_backend_entry)
    reports = [run(url, model, label, entry) for label, url, model, entry in candidates]

    if args.output:
        # What each backend entry added to every request -- never a key value.
        config = {
            "backend_entry": {
                label: entry_config(entry) for label, _, _, entry in candidates
            }
        }
        write_report(
            args.output,
            "bench_embeddings",
            config,
            reports,
            candidates[0][1] if candidates else None,
            (os.path.abspath(__file__), "provenance.py"),
        )
        print(f"\n  Report written to {args.output}")


if __name__ == "__main__":
    main()
