"""How fast does a lane decode once its context is deep?

A reply's decode rate is not one number. GenieX v0.7.0's CPU lane took the
thinking 4B from 31.6 to 10.0 tok/s inside one 2048-token reply
(2026-09-23-geniex-upgrade/v070r2-probes), and after ~7.2k tokens of context
the 9B decoded at ~9 tok/s where both 4B GGUFs managed 3.0-3.4
(benchmarks/docs/roadmap-campaign-2026-09-24.md, P7.2). The speed runner
pools each reply into one rate and shows neither.

`orchestrant-bench depth` sends ONE streamed request after a filler prompt of
about `--context-tokens`, stamps every generated delta, and reports the time
to the first (the prefill at that depth) and the rate over each `--window` of
generated tokens. It is the campaign's scratch script made a lab tool: the
same prompt, request, windows and fields, so the tracked `*-depth-8k.json`
traces stay comparable -- in the shared envelope, whose provenance those files
lack (runtime, model files, host load, tool hash, command line).

    orchestrant-bench depth --backend geniex-cpu --model <id> --output cpu-9b-depth-8k.json
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
import time

from orchestrant.benchmark.answers import MIN_DECODE_WINDOW_S, delta_pieces
from orchestrant.benchmark.client import http_error_detail, post_json
from orchestrant.benchmark.contract import filler


# The scratch script's filler seed and question: the tracked traces were
# measured on exactly this prompt, and a new trace compares only while it is.
FILLER_SEED = 7
QUESTION = (
    "Write a detailed technical blog post about the benefits and challenges of "
    "running large language models locally."
)
# This file, and answers.py for what counts as a generated token.
TOOL_FILES = ("depth.py", "answers.py")


def depth_prompt(context_tokens):
    """About `context_tokens` of the contract's fixed filler, then QUESTION."""
    if not context_tokens:
        return QUESTION
    notes = filler(context_tokens, seed=FILLER_SEED)
    return f"Background notes, for reference only:\n\n{notes}\n\n---\n\n{QUESTION}"


def windows(stamps, size=256):
    """The rate over each `size` generated tokens, cut as the scratch script cut them.

    Stamp 0 ends the prefill and starts no window; each window runs from one
    stamp to the one `size` later, the last to the final stamp, and a tail of
    under a quarter window is dropped (64 of 256, the script's floor). A
    window shorter than answers.MIN_DECODE_WINDOW_S arrived in one burst and
    gets no rate: Ollama sent whole replies that way (the campaign's defect 2).
    """
    out, last = [], len(stamps) - 1
    for start in range(1, last, size):
        end = min(start + size, last)
        if end - start < size // 4:
            break
        seconds = stamps[end] - stamps[start]
        burst = seconds < MIN_DECODE_WINDOW_S
        rate = None if burst else round((end - start) / seconds, 2)
        out.append({"tokens": f"{start}-{end}", "tok_per_s": rate})
    return out


def _error(exc):
    """An HTTP error with its body, anything else by type: never just a status."""
    detail = http_error_detail(exc)
    if detail:
        return f"HTTP {detail[0]}: {detail[1][:300]}"
    return f"{type(exc).__name__}: {exc}"[:300]


def warm(base_url, model, entry=None, timeout=3600):
    """The scratch script's spacer -> {"seconds", "error"}.

    It loads the model and makes the measured request no identical follow-up.
    Not client.spacer: that gives up after 120 s and says nothing, and a model
    still loading when the measured request goes out puts its load into that
    request's TTFT. So it waits as long as the measured request would, and
    its outcome is recorded.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 1,
    }
    t0, error = time.monotonic(), None
    try:
        url = f"{base_url}/v1/chat/completions"
        with post_json(url, body, entry=entry, timeout=timeout) as r:
            r.json()
    except Exception as e:  # recorded; the measured request reports its own
        error = _error(e)
    return {"seconds": round(time.monotonic() - t0, 2), "error": error}


def _chunks(lines):
    """The parsed chunks of an SSE stream, up to [DONE].

    A server error inside the already-200 stream raises. Skipped, it read as
    a short, clean trace (error None, exit 0); bench_coding graded the same
    fault "no code found in reply" until it raised too.
    """
    for line in lines:
        if line.startswith("error:"):
            raise RuntimeError(f"server error in stream: {line[6:].strip()[:200]}")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            fault = json.dumps(chunk["error"])[:200]
            raise RuntimeError(f"server error in stream: {fault}")
        yield chunk


def measure(base_url, model, context_tokens, max_tokens, *, entry=None, timeout=3600):
    """One streamed reply after the filler -> (stamps, state).

    `stamps` are seconds from sending the request to each delta that carries
    text, thinking included (thinking is output). `state` holds the usage,
    finish_reason and an error; what arrived before an error is kept. A reply
    with no generated token is an error too: nothing was measured.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": depth_prompt(context_tokens)}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    stamps, state = [], {"usage": None, "finish_reason": None, "error": None}
    t0 = time.monotonic()
    try:
        url = f"{base_url}/v1/chat/completions"
        with post_json(url, body, entry=entry, stream=True, timeout=timeout) as r:
            for chunk in _chunks(r.lines()):
                state["usage"] = chunk.get("usage") or state["usage"]
                for choice in chunk.get("choices") or []:
                    finish = choice.get("finish_reason")
                    state["finish_reason"] = finish or state["finish_reason"]
                    if any(delta_pieces(choice.get("delta") or {})):
                        stamps.append(time.monotonic() - t0)
    except Exception as e:  # a partial trace is still evidence; the error says why
        state["error"] = _error(e)
    if not stamps and not state["error"]:
        # A lane that ignored "stream" answers one JSON body, which has no
        # "data:" line; the report must not read as a clean trace.
        finish = state["finish_reason"]
        state["error"] = f"no generated token arrived (finish_reason={finish!r})"
    return stamps, state


def trace_row(model, context_tokens, window, stamps, state, warmup):
    """One report row: the scratch script's six fields first, then what it lacked."""
    return {
        "model": model,
        "context_tokens_requested": context_tokens,
        "usage": state["usage"],
        "ttft_s": round(stamps[0], 2) if stamps else None,
        "tokens": len(stamps),
        "windows": windows(stamps, window),
        "finish_reason": state["finish_reason"],
        "error": state["error"],
        "warmup": warmup,
        # The raw stamps, so a trace can be re-cut at another window size.
        "delta_times_s": [round(s, 3) for s in stamps],
    }


def summary_line(row):
    """The console line: prefill at depth, then the window rates."""
    prompt = (row["usage"] or {}).get("prompt_tokens")
    ttft = row["ttft_s"]
    prefill = f" ({prompt / ttft:.1f} tok/s prefill)" if prompt and ttft else ""
    rates = [w["tok_per_s"] for w in row["windows"]]
    line = f"  TTFT {ttft} s after {prompt} prompt tokens{prefill}; "
    line += f"{row['tokens']} tokens, windows {rates} tok/s"
    return line + (f"\n  ERROR {row['error']}" if row["error"] else "")


def _parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--backend", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument(
        "--context-tokens",
        type=int,
        default=8000,
        help="about this many tokens of filler before the question (default "
        "8000: 7154-7159 prompt tokens on the Qwen3 tokenizers)",
    )
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument(
        "--window", type=int, default=256, help="tokens per rate window (default 256)"
    )
    ap.add_argument(
        "--rest",
        type=float,
        default=30,
        help="seconds between the warm-up and the measured request, spent "
        "reading the host load (default 30)",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="per-request timeout in seconds (default 3600: Ollama's 9B at 4 "
        "threads took 611.6 s to its first token at ~7.2k tokens)",
    )
    ap.add_argument("--output", default=None, help="write the report as JSON")
    return ap


def main():
    from orchestrant.benchmark.client import entry_config, run_start, write_report
    from orchestrant.benchmark.openai_api import (
        detect_model_via_api,
        resolve_backend,
        resolve_backend_entry,
        resolve_model,
    )

    ap = _parser()
    args = ap.parse_args()
    if args.window < 1:
        ap.error("--window must be at least 1")
    base_url, backend_model, _ = resolve_backend(args.backend, args.base_url)
    entry = resolve_backend_entry(args.backend, args.base_url)
    detect = functools.partial(detect_model_via_api, base_url)
    model = resolve_model(args.model, backend_model, entry, detect)
    print(f"\n  Depth trace: {model} @ {base_url}, ~{args.context_tokens} tokens\n")
    warmup = warm(base_url, model, entry, args.timeout)
    # The rest doubles as the host-load window: the load that slows a CPU lane
    # is the load just before the measured request. Slept out if cut short.
    rested = time.monotonic()
    started = run_start(TOOL_FILES, base_url, seconds=args.rest)
    time.sleep(max(0.0, args.rest - (time.monotonic() - rested)))
    stamps, state = measure(
        base_url,
        model,
        args.context_tokens,
        args.max_tokens,
        entry=entry,
        timeout=args.timeout,
    )
    row = trace_row(model, args.context_tokens, args.window, stamps, state, warmup)
    # Written before anything that merely prints (write_report's rule).
    if args.output:
        prompt = depth_prompt(args.context_tokens).encode()
        config = {
            "context_tokens": args.context_tokens,
            "max_tokens": args.max_tokens,
            "window": args.window,
            "rest_s": args.rest,
            "timeout_s": args.timeout,
            "filler_seed": FILLER_SEED,
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "backend_entry": entry_config(entry),
        }
        label = args.backend or model
        write_report(
            args.output,
            "bench_depth",
            config,
            [{"label": label, **row}],
            base_url,
            TOOL_FILES,
            run_start=started,
        )
    print(summary_line(row))
    if args.output:
        print(f"\n  Report written to {args.output}")
    return 1 if row["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
