"""Run the contract checks: the table, the run, the diff and the command line.

orchestrant-bench contract --backend geniex-npu --overflow-tokens 6000 --output npu.json
orchestrant-bench contract --diff v061-npu.json v070-npu.json
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import time

from orchestrant.benchmark.contract import (
    DEFAULT_TIMEOUT_S,
    SLOWEST_PREFILL_TOK_S,
    check_bundle_system_prompt,
    check_cached_prompt_tokens,
    check_completions_stop,
    check_greedy,
    check_identical_repeat,
    check_max_tokens,
    check_output_cap,
    check_overflow,
    check_power_mode,
    check_prefix_cache,
    check_prefix_extend,
    check_prefix_fork,
    check_response_format,
    check_seed,
    check_stop,
    check_stream_usage,
    check_temperature0,
    check_temperature0_is_greedy,
    check_thinks,
    check_tool_calls,
    check_usage,
    prompt_timeout,
)


CHECKS = (
    (
        "max_tokens_honoured",
        "Does the server stop at max_tokens with finish_reason 'length'?",
        check_max_tokens,
    ),
    (
        "output_cap",
        "Does the server stop earlier than a 3000-token max_tokens asks?",
        check_output_cap,
    ),
    (
        "usage_reported",
        "Does a non-streamed reply carry usage token counts?",
        check_usage,
    ),
    (
        "prompt_tokens_survive_cache",
        "Is usage.prompt_tokens still the prompt's size when the prompt is cached?",
        check_cached_prompt_tokens,
    ),
    (
        "stream_usage",
        "Does a stream honour stream_options.include_usage?",
        check_stream_usage,
    ),
    (
        "temperature0_deterministic",
        "Do two T=0 requests (another between them) return identical text?",
        check_temperature0,
    ),
    (
        "greedy_deterministic",
        "Do two temperature-0.01/top_k-1 requests (another between) agree?",
        check_greedy,
    ),
    (
        "temperature0_is_greedy",
        "Is temperature 0 greedy decoding rather than 'unset'?",
        check_temperature0_is_greedy,
    ),
    (
        "identical_repeat_intact",
        "Does an identical request sent twice in a row get the same reply?",
        check_identical_repeat,
    ),
    (
        "seed_deterministic",
        "Do two T=1.0 requests with the same seed (another between) agree?",
        check_seed,
    ),
    (
        "emits_think",
        "Does a plain question come back with a <think> block?",
        check_thinks,
    ),
    ("chat_stop_honoured", "Does chat honour a stop sequence?", check_stop),
    (
        "completions_stop_honoured",
        "Does /v1/completions accept and honour a stop sequence?",
        check_completions_stop,
    ),
    (
        "tool_calls_parsed",
        "Is a tool call returned as tool_calls rather than text?",
        check_tool_calls,
    ),
    (
        "response_format_json_schema",
        "Is a response_format json_schema honoured (not ignored or refused)?",
        check_response_format,
    ),
    (
        "prefix_cache",
        "Is an identical request reused rather than re-prefilled?",
        check_prefix_cache,
    ),
    (
        "prefix_cache_extend",
        "Is a conversation extended by one turn served from the cache?",
        check_prefix_extend,
    ),
    (
        "prefix_cache_fork",
        "Is a shared long prefix with a different tail served from the cache?",
        check_prefix_fork,
    ),
    (
        "overflow_is_clean_error",
        "Is an over-long prompt refused with an error naming the context?",
        check_overflow,
    ),
    (
        "power_mode_understood",
        "Is a per-request power_mode validated (valid accepted, invalid refused)?",
        check_power_mode,
    ),
    (
        "bundle_system_prompt",
        "With no system message, is a default system prompt sent anyway?",
        check_bundle_system_prompt,
    ),
)


def run(
    base_url,
    model,
    entry=None,
    prefix_tokens=2000,
    overflow_tokens=0,
    only=None,
    timeout=None,
):
    ctx = {
        "base_url": base_url,
        "model": model,
        "entry": entry or {},
        "prefix_tokens": prefix_tokens,
        "overflow_tokens": overflow_tokens,
        "timeout": timeout,
    }
    results = []
    for check_id, question, fn in CHECKS:
        if only and check_id not in only:
            continue
        t0 = time.monotonic()
        try:
            outcome = fn(ctx)
        except Exception as e:  # one broken check must not cost the others
            outcome = {"answer": "error", "evidence": f"{type(e).__name__}: {e}"[:300]}
        outcome = {"id": check_id, "question": question, **outcome}
        outcome["seconds"] = round(time.monotonic() - t0, 2)
        results.append(outcome)
        print(
            f"  {check_id:<28} {outcome['answer']:<8} {outcome['seconds']:>7.1f}s  {str(outcome['evidence'])[:90]}"
        )
    return results


def diff(old, new):
    """Rows (check, old answer, new answer) for every check whose answer moved."""

    def index(report):
        rows = (report.get("reports") or [{}])[0].get("checks") or []
        return {r["id"]: r for r in rows}

    a, b = index(old), index(new)
    order = [c[0] for c in CHECKS]
    rows = []
    for check_id in sorted(
        set(a) | set(b), key=lambda k: order.index(k) if k in order else len(order)
    ):
        before = (a.get(check_id) or {}).get("answer", "-")
        after = (b.get(check_id) or {}).get("answer", "-")
        rows.append((check_id, before, after, before != after))
    return rows


def _read_report(path):
    """One report for --diff; a format this checkout does not know ends the run and names the file."""
    from orchestrant.benchmark.client import check_result_format

    with open(path) as f:
        report = json.load(f)
    try:
        check_result_format(report)
    except ValueError as e:
        raise SystemExit(f"{path}: {e}") from e
    return report


def _parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--backend", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--prefix-tokens",
        type=int,
        default=2000,
        help="size of the prefix-cache probe (default 2000)",
    )
    parser.add_argument(
        "--overflow-tokens",
        type=int,
        default=0,
        help="send a prompt of about this many tokens to see how overflow is reported; "
        "0 skips it (on a 16k GGUF lane it would prefill for minutes)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT_S}); the "
        f"prefix-cache and overflow requests get at least their prompt's tokens / "
        f"{SLOWEST_PREFILL_TOK_S}, the output cap at least 1800",
    )
    parser.add_argument("--only", default=None, help="comma-separated check ids")
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--diff", nargs=2, metavar=("OLD", "NEW"), help="compare two contract reports"
    )
    return parser


def main():
    from orchestrant.benchmark.client import entry_config, run_start, write_report
    from orchestrant.benchmark.openai_api import (
        detect_model_via_api,
        resolve_backend,
        resolve_backend_entry,
        resolve_model,
    )
    from orchestrant.benchmark.provenance import compare as compare_provenance

    args = _parser().parse_args()

    if args.diff:
        old, new = (_read_report(path) for path in args.diff)
        for note in compare_provenance(
            old.get("provenance") or {}, new.get("provenance") or {}
        ):
            print(f"  ! {note}")
        changed = 0
        for check_id, before, after, moved in diff(old, new):
            changed += moved
            print(
                f"  {'CHANGED' if moved else '       '}  {check_id:<28} {before:<8} -> {after}"
            )
        return 1 if changed else 0

    base_url, backend_model, _ = resolve_backend(args.backend, args.base_url)
    entry = resolve_backend_entry(args.backend, args.base_url)
    # Detect on the probed lane, not the module default (localhost:11434).
    detect = functools.partial(detect_model_via_api, base_url)
    model = resolve_model(args.model, backend_model, entry, detect)
    print(f"\n  Contract probe: {model} @ {base_url}\n")
    tool_files = ("contract.py", "contract_cli.py")
    started = run_start(tool_files, base_url)
    only = set(args.only.split(",")) if args.only else None
    checks = run(
        base_url,
        model,
        entry,
        args.prefix_tokens,
        args.overflow_tokens,
        only,
        timeout=args.timeout,
    )
    if args.output:
        write_report(
            args.output,
            "bench_contract",
            {
                "prefix_tokens": args.prefix_tokens,
                "overflow_tokens": args.overflow_tokens,
                "timeout_s": args.timeout or DEFAULT_TIMEOUT_S,
                "prefix_timeout_s": prompt_timeout(args.prefix_tokens, args.timeout),
                "overflow_timeout_s": prompt_timeout(
                    args.overflow_tokens, args.timeout
                ),
                "backend_entry": entry_config(entry),
            },
            [{"label": args.backend or model, "model": model, "checks": checks}],
            base_url,
            tool_files,
            run_start=started,
        )
        print(f"\n  Report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
