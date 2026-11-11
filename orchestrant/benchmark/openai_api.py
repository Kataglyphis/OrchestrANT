#!/usr/bin/env python3
"""Benchmark any OpenAI-compatible LLM endpoint, with CPU and RAM tracking.

Usage:
    orchestrant-bench speed
    orchestrant-bench speed --model gemma4:26b --prompts 10
    orchestrant-bench speed --output benchmark_results.json
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any, NoReturn

from orchestrant.benchmark import answers, correctness, speed_summary
from orchestrant.benchmark.answers import Reply, accounting, from_body, read_stream
from orchestrant.benchmark.client import (
    entry_config,
    error_text,
    post_json,
    request_headers,
    run_start,
)
from orchestrant.benchmark.correctness import CORRECTNESS_PROBES, print_correctness
from orchestrant.benchmark.energy import EnergyMeter, energy_block, energy_lines, renet
from orchestrant.benchmark.host_info import collect_hardware_info, top_cpu_processes
from orchestrant.benchmark.hostload import LaneProcess, RequestBracket, open_meters
from orchestrant.benchmark.provenance import collect_or_error
from orchestrant.benchmark.speed_cli import speed_parser
from orchestrant.benchmark.speed_prompts import (
    LONG_PROMPTS,
    MEDIUM_PROMPTS,
    SHORT_PROMPTS,
)
from orchestrant.benchmark.speed_table import print_table


# Hashed into tool_sha256: every file that decides what a speed row says.
SPEED_TOOL_FILES = (
    "openai_api.py",
    "answers.py",
    "correctness.py",
    "energy.py",
    "hostload.py",
    "host_info.py",
    "speed_cli.py",
    "speed_table.py",
    "speed_prompts.py",
)


# LLM_BASE_URL is the name to use; OLLAMA_BASE_URL still works for existing scripts.
LLM_BASE_URL = (
    os.environ.get("LLM_BASE_URL")
    or os.environ.get("OLLAMA_BASE_URL")
    or "http://localhost:11434"
)
OLLAMA_BASE_URL = LLM_BASE_URL  # backwards-compatible alias
GLANCES_URL = os.environ.get("GLANCES_URL", "http://localhost:61208")


# Named backends; sibling tools import these. See docs/source/benchmark.rst § Named backends


def _default_backends_file():
    """Where the registry lives: LLM_BACKENDS, else the hub checkout's, else a local copy."""
    env = os.environ.get("LLM_BACKENDS")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    hub = os.path.join(
        repo, "third_party", "ANTfrastructure", "linux", "llm-stack", "backends.json"
    )
    return hub if os.path.isfile(hub) else os.path.join(here, "backends.json")


BACKENDS_FILE = _default_backends_file()


def load_backends(path=None):
    """Read backends.json; missing or malformed gives an empty registry, never raises."""
    path = path or BACKENDS_FILE
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("backends", {}), data.get("default")
    except FileNotFoundError:
        return {}, None
    except Exception as e:
        print(
            f"  WARNING: could not read {path} ({e}); named backends unavailable",
            file=sys.stderr,
        )
        return {}, None


def resolve_backend(name=None, base_url=None, path=None):
    """Return (base_url, default_model, source) following the order above."""
    backends, default_name = load_backends(path)

    if base_url:
        return base_url.rstrip("/"), None, "--base-url"

    env = os.environ.get("LLM_BASE_URL") or os.environ.get("OLLAMA_BASE_URL")
    if env:
        # A named backend still supplies its default model, if it matches.
        entry = backends.get(name) if name else None
        return env.rstrip("/"), (entry or {}).get("model"), "environment"

    if name:
        if name not in backends:
            known = ", ".join(sorted(backends)) or "(none configured)"
            raise SystemExit(f"unknown backend {name!r}. Known: {known}")
        entry = backends[name]
        return entry["base_url"].rstrip("/"), entry.get("model"), f"backend {name!r}"

    if default_name and default_name in backends:
        entry = backends[default_name]
        return (
            entry["base_url"].rstrip("/"),
            entry.get("model"),
            f"default backend {default_name!r}",
        )

    return "http://localhost:11434", None, "built-in default"


def resolve_backend_entry(name=None, base_url=None, path=None):
    """The whole backends.json entry; a bare --base-url gets {} so no API key rides along."""
    backends, default_name = load_backends(path)
    if name:
        if name not in backends:
            known = ", ".join(sorted(backends)) or "(none configured)"
            raise SystemExit(f"unknown backend {name!r}. Known: {known}")
        return dict(backends[name])
    if base_url:
        return {}
    if default_name and default_name in backends:
        return dict(backends[default_name])
    return {}


def print_backends(path=None):
    backends, default_name = load_backends(path)
    if not backends:
        print("  No backends configured (backends.json missing or empty).")
        return
    print("\n  Configured backends:")
    for name in sorted(backends):
        entry = backends[name]
        mark = " (default)" if name == default_name else ""
        print(f"    {name}{mark}")
        print(f"      url:   {entry['base_url']}")
        if entry.get("model"):
            print(f"      model: {entry['model']}")
        if entry.get("note"):
            print(f"      note:  {entry['note']}")
    print()


def get_glances_data(endpoint):
    """Fetch JSON data from the Glances REST API (v4, falling back to v3)."""
    import requests

    for api_ver in ("4", "3"):
        try:
            r = requests.get(f"{GLANCES_URL}/api/{api_ver}/{endpoint}", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception:  # nosec B112 -- tries the next API version
            continue
    return None


def sample_resources_psutil():
    """Sample CPU and RAM via psutil on the host."""
    import psutil

    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": cpu,
        "ram_percent": mem.percent,
        "ram_used_gb": mem.used / (1024**3),
        "ram_total_gb": mem.total / (1024**3),
    }


def sample_resources_glances():
    """Sample CPU and RAM via the Glances REST API."""
    cpu_data = get_glances_data("cpu")
    mem_data = get_glances_data("mem")
    if cpu_data is None or mem_data is None:
        return None
    return {
        "cpu_percent": cpu_data.get("total", 0.0),
        "ram_percent": mem_data.get("percent", 0.0),
        "ram_used_gb": mem_data.get("used", 0) / (1024**3),
        "ram_total_gb": mem_data.get("total", 0) / (1024**3),
    }


_gpu_probe = None  # one probe per process: driver contexts are not cheap
# Two first callers on two threads would each open a probe and leak one.
_gpu_probe_lock = threading.Lock()


def gpu_probe():
    """The process-wide GPU probe, or None; False memoizes "none available"."""
    global _gpu_probe
    if _gpu_probe is None:
        with _gpu_probe_lock:
            if _gpu_probe is None:
                try:
                    from orchestrant.monitoring.gpu import GPUProbe

                    probe = GPUProbe()
                    _gpu_probe = probe if probe.available else False
                except Exception:
                    _gpu_probe = False
    return _gpu_probe or None


def sample_gpu_resources():
    """Local GPU utilization, VRAM and power; {} when unavailable, and never raises."""
    try:
        probe = gpu_probe()
        if probe is None:
            return {}
        snapshot = probe.read()
        if snapshot is None:
            return {}
        return {
            "gpu_utilization_percent": round(snapshot.utilization, 1),
            "gpu_memory_used_gb": round(snapshot.memory_used_bytes / (1024**3), 2),
            "gpu_power_watts": round(snapshot.power_watts, 1),
        }
    except Exception:
        return {}


def _get_json(url, entry=None, timeout=5):
    """GET one JSON document with the backend entry's auth and headers."""
    req = urllib.request.Request(url, headers=request_headers(entry))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 -- endpoint URL from config
            return json.load(r)
    except urllib.error.HTTPError as e:
        # The error holds the response open until GC, and every caller only needs it raised.
        e.close()
        raise


def list_models_via_api(base_url=None, entry=None):
    """Every model id the endpoint lists: /v1/models, else Ollama's /api/tags."""
    base = base_url or LLM_BASE_URL
    try:
        models = _get_json(f"{base}/v1/models", entry).get("data", [])
        if models:
            return [m["id"] for m in models]
    except Exception:  # nosec B110 -- best-effort probe
        pass
    # Ollama-native fallback: /api/tags lists what is actually pulled.
    try:
        tags = _get_json(f"{base}/api/tags", entry).get("models", [])
        if tags:
            return [t.get("name") or t.get("model", "unknown") for t in tags]
    except Exception:  # nosec B110 -- best-effort probe
        pass
    return []


def detect_model_via_api(base_url=None, entry=None):
    """The model an endpoint serves, when that can be known."""
    models = list_models_via_api(base_url, entry)
    # A listing is not a loaded model, and hot-loading a guess can crash an NPU lane.
    if len(models) > 1:
        shown = ", ".join(models[:6])
        if len(models) > 6:
            shown += f", ... ({len(models)} in all)"
        raise SystemExit(
            f"{base_url or LLM_BASE_URL} lists {len(models)} models and does not "
            f"say which one is loaded: {shown}. Pass --model, or use a --backend "
            f"that names one."
        )
    return models[0] if models else "unknown"


def resolve_model(explicit, backend_model, entry, detect=None):
    """--model, else the backend's default, else ask the endpoint (never a probe:false one)."""
    if explicit or backend_model:
        return explicit or backend_model
    if not (entry or {}).get("probe", True):
        raise SystemExit(
            "this backend is marked probe:false, so /v1/models is not queried. "
            'Pass --model, or give the entry a "model" field in backends.json.'
        )
    return (detect or detect_model_via_api)(entry=entry)


def _warmup(
    endpoint: str,
    model: str,
    extra_params: dict[str, Any] | None,
    entry: dict[str, Any] | None,
) -> None:
    """One short request to load the model into memory."""
    try:
        wp_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Warmup"}],
            "max_tokens": 1,
        }
        if extra_params:
            wp_payload.update(extra_params)
        with post_json(endpoint, wp_payload, entry=entry, timeout=120) as r:
            r.raw.read()
    except SystemExit:
        raise  # a missing API key is a setup error, not a warmup hiccup
    except Exception:  # nosec B110 -- best-effort warmup
        pass
    time.sleep(1)


def _chat_payload(
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    *,
    stream: bool,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """The request body for one measured prompt."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if extra_params:
        payload.update(extra_params)
    # Request usage in streaming mode (Ollama supports this)
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _send_chat(
    endpoint: str,
    payload: dict[str, Any],
    entry: dict[str, Any] | None,
    *,
    stream: bool,
) -> Reply:
    """POST one measured request and parse its reply, streamed or whole."""
    if stream:
        with post_json(
            endpoint, payload, entry=entry, stream=True, timeout=300
        ) as response:
            return read_stream(response.lines())
    with post_json(endpoint, payload, entry=entry, timeout=300) as r:
        return from_body(r.json())


def _token_counts(reply: Reply) -> tuple[int, int, int, bool]:
    """Prompt, completion and total tokens, and whether completion was counted from deltas."""
    usage, streamed_chunks = reply.usage, reply.chunks
    prompt_tokens = usage.get("prompt_tokens", 0) if usage else 0
    completion_tokens = usage.get("completion_tokens", 0) if usage else 0
    total_tokens = usage.get("total_tokens", 0) if usage else 0

    # Some servers ignore include_usage; counted content deltas are flagged as estimated.
    tokens_estimated = False
    if not completion_tokens and streamed_chunks:
        completion_tokens = streamed_chunks
        total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        tokens_estimated = True
    return prompt_tokens, completion_tokens, total_tokens, tokens_estimated


def _chat_row(
    i: int,
    prompt: str,
    reply: Reply,
    start: float,
    elapsed: float,
    max_tokens: int,
    bracket: RequestBracket,
) -> dict[str, Any]:
    """The result row of one answered prompt."""
    prompt_tokens, completion_tokens, total_tokens, tokens_estimated = _token_counts(
        reply
    )
    tokens_per_sec = completion_tokens / elapsed if elapsed > 0 else 0.0

    # Split prefill from decode: the first token of any kind, thinking included, ends it.
    first = reply.first_token_at
    ttft = (first - start) if first is not None else None
    ttfa = reply.first_answer_at - start if reply.first_answer_at else None
    prefill_tps = prompt_tokens / ttft if ttft and prompt_tokens else None

    # A reasoning model can be fastest per token and slowest to a usable answer.
    acct = accounting(reply, completion_tokens, max_tokens)

    return {
        "prompt_index": i,
        "prompt_preview": prompt[:60],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tokens_estimated": tokens_estimated,
        "tokens_per_sec": round(tokens_per_sec, 2),
        # The metric to rank by; None when the budget ran out before an answer.
        "wall_s_to_answer": round(elapsed, 2) if acct["answered"] else None,
        "latency_s": round(elapsed, 2),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "ttfa_s": round(ttfa, 3) if ttfa is not None else None,
        **answers.decode_fields(elapsed, ttft, completion_tokens),
        "prefill_tok_per_sec": round(prefill_tps, 1) if prefill_tps else None,
        **acct,
        "content_preview": (reply.content or reply.reasoning)[:80],
        **bracket.fields(completion_tokens),
    }


def benchmark_chat(
    model,
    prompts,
    *,
    max_tokens=256,
    temperature=0.0,
    stream=False,
    extra_params=None,
    warmup=True,
    base_url=None,
    entry=None,
    lane=None,
    meter=None,
    idle_seconds=5.0,
):
    """Yield timing and resource rows per prompt from the chat completions endpoint.

    `lane` and `meter` work only when this process shares a host with the server.
    """
    endpoint = f"{base_url or LLM_BASE_URL}/v1/chat/completions"
    if warmup:
        _warmup(endpoint, model, extra_params, entry)

    idle_w = meter.idle_power(idle_seconds) if meter and meter.available else None

    for i, prompt in enumerate(prompts):
        payload = _chat_payload(
            model,
            prompt,
            max_tokens,
            temperature,
            stream=stream,
            extra_params=extra_params,
        )

        # Measured over the request rather than sampled around it.
        bracket = RequestBracket(
            sample_resources, top_cpu_processes, lane, meter, idle_w
        ).start()
        start = time.monotonic()

        try:
            reply = _send_chat(endpoint, payload, entry, stream=stream)
        except Exception as e:
            yield {
                "prompt_index": i,
                "prompt_preview": prompt[:60],
                "error": error_text(e),
                "latency_s": time.monotonic() - start,
            }
            continue

        elapsed = time.monotonic() - start
        bracket.stop()
        yield _chat_row(i, prompt, reply, start, elapsed, max_tokens, bracket)

    # A second idle baseline: idle draw drifts, so main() nets rows against the mean.
    if idle_w is not None and meter is not None:
        meter.idle_power(idle_seconds)


def run_correctness_probe(
    model, *, max_tokens=4000, extra_params=None, base_url=None, entry=None
):
    """Check the model still answers correctly at temperature 0; None if unreachable.

    `max_tokens` is high because a reply cut off inside <think> scores WRONG.
    """
    endpoint = f"{base_url or LLM_BASE_URL}/v1/chat/completions"
    items = []
    for probe in CORRECTNESS_PROBES:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": probe.prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if extra_params:
            payload.update(extra_params)
        try:
            with post_json(endpoint, payload, entry=entry, timeout=600) as r:
                reply = from_body(r.json())
        except Exception as e:
            items.append(correctness.errored_item(probe, e))
            continue
        items.append(correctness.graded_reply(probe, reply))
    return correctness.summarise(items)


_sampler_warned = False
# A Glances that did not answer is not asked again: each refused connect costs seconds.
_glances_down = False


def sample_resources():
    """Unified resource sampler: Glances API → psutil (+ GPU) → zeros; never raises."""
    global _sampler_warned, _glances_down
    gpu = sample_gpu_resources()
    result = None
    try:
        result = None if _glances_down else sample_resources_glances()
    except Exception:
        result = None  # requests missing or Glances flaky: fall through to psutil
    _glances_down = result is None
    if result is None:
        try:
            result = sample_resources_psutil()
        except Exception as e:
            if not _sampler_warned:
                _sampler_warned = True
                print(
                    f"  WARNING: resource sampling failed ({e!r}); "
                    "reporting zeros for CPU/RAM from now on",
                    file=sys.stderr,
                )
            result = {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "ram_used_gb": 0.0,
                "ram_total_gb": 0.0,
            }
    result.update(gpu)
    return result


def _show_loaded(path: str) -> None:
    """Print a saved result file's table instead of running."""
    with open(path) as f:
        data = json.load(f)
    print(f"\n  Loaded benchmark results from {path}")
    print(f"  Model: {data.get('model', '?')}  |  Date: {data.get('timestamp', '?')}")
    print()
    print_table(data.get("results", []))


def _correctness_only(
    args: argparse.Namespace, model: str, source: str, entry: dict[str, Any] | None
) -> NoReturn:
    """Run only the correctness probe and exit with its verdict."""
    print(f"\n  Model: {model}")
    print(f"  API:   {LLM_BASE_URL}/v1  (from {source})")
    probe = run_correctness_probe(
        model,
        max_tokens=args.correctness_max_tokens,
        extra_params=json.loads(args.extra_params) if args.extra_params else None,
        entry=entry,
    )
    print_correctness(probe)
    # Distinct exit codes: "model is wrong" vs "we cut it off" (integrity items only).
    sys.exit(correctness.exit_code(probe))


def _run_prompts(
    args: argparse.Namespace,
    model: str,
    all_prompts: list[str],
    extra_params: dict[str, Any] | None,
    entry: dict[str, Any] | None,
    lane: LaneProcess,
    meter: EnergyMeter,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Measure every prompt; returns the rows, whether Ctrl-C cut the run, and the side file."""
    # Crash-safe side file; .jsonl so the *.json globs never take it for a result.
    partial_path = f"{args.output}.partial.jsonl" if args.output else None
    if partial_path and os.path.exists(partial_path):
        os.remove(partial_path)  # stale leftover from an aborted run

    results = []
    interrupted = False
    try:
        for result in benchmark_chat(
            model,
            all_prompts,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            stream=args.stream,
            extra_params=extra_params,
            warmup=not args.no_warmup,
            entry=entry,
            lane=lane,
            meter=meter,
            idle_seconds=args.idle_seconds,
        ):
            results.append(result)
            if partial_path:
                with open(partial_path, "a") as pf:
                    pf.write(json.dumps(result) + "\n")
    except KeyboardInterrupt:
        interrupted = True
        print(f"\n  Interrupted — {len(results)}/{len(all_prompts)} prompts completed.")
        if partial_path:
            print(f"  Completed results preserved in {partial_path}")
    return results, interrupted, partial_path


def _write_output(path: str, output: dict[str, Any], partial_path: str | None) -> None:
    """Atomic write: readers of *.json must never see a half-written file."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(output, f, indent=2)
    os.replace(tmp_path, path)
    if partial_path and os.path.exists(partial_path):
        os.remove(partial_path)
    print(f"\n  Results written to {path}")


def _announce(args: argparse.Namespace, model: str, source: str) -> list[str]:
    """Print the run header; returns the prompts --prompts selects."""
    print(f"\n  Model: {model}")
    print(f"  API:   {LLM_BASE_URL}/v1  (from {source})")
    print(f"  Glances: {GLANCES_URL}/api/4 (v3 fallback)")
    print()

    all_prompts = SHORT_PROMPTS + MEDIUM_PROMPTS + LONG_PROMPTS
    if args.prompts > 0:
        all_prompts = all_prompts[: args.prompts]

    print(
        f"  Running {len(all_prompts)} benchmark prompts (max_tokens={args.max_tokens}, stream={args.stream})"
    )
    print()
    return all_prompts


def _checked_correctness(
    args: argparse.Namespace,
    model: str,
    extra_params: dict[str, Any] | None,
    entry: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The post-run correctness block, printed."""
    checked = run_correctness_probe(
        model,
        max_tokens=args.correctness_max_tokens,
        extra_params=extra_params,
        entry=entry,
    )
    print_correctness(checked)
    return checked


def main():
    args = speed_parser(__doc__).parse_args()

    if args.list_backends:
        print_backends()
        return

    # Rebinding the module global keeps every existing call site working.
    global LLM_BASE_URL, OLLAMA_BASE_URL
    LLM_BASE_URL, backend_model, source = resolve_backend(args.backend, args.base_url)
    OLLAMA_BASE_URL = LLM_BASE_URL
    entry = resolve_backend_entry(args.backend, args.base_url)

    if args.load:
        _show_loaded(args.load)
        return

    # A backend may name its own default model; an explicit --model still wins.
    model = resolve_model(args.model, backend_model, entry)

    if args.correctness_only:
        _correctness_only(args, model, source, entry)

    all_prompts = _announce(args, model, source)

    extra_params = json.loads(args.extra_params) if args.extra_params else None

    # The start hash, time and host load every other tool records.
    start = run_start(SPEED_TOOL_FILES, LLM_BASE_URL)
    # Only meaningful on the lane's host; otherwise the report records why, not zeros.
    lane, meter = open_meters(LLM_BASE_URL, energy=not args.no_energy)
    results, interrupted, partial_path = _run_prompts(
        args, model, all_prompts, extra_params, entry, lane, meter
    )

    meter.stop()
    renet(results, meter)
    print_table(results)
    for line in energy_lines(meter):
        print(line)
    checked = None
    if args.correctness and not interrupted:
        checked = _checked_correctness(args, model, extra_params, entry)

    output = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": model,
        "api_url": f"{LLM_BASE_URL}/v1",
        "backend": args.backend,
        "hardware": collect_hardware_info(),
        "config": {
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "stream": args.stream,
            "extra_params": extra_params,
            # Names only for header and key fields: a value could be the key itself.
            "backend_entry": entry_config(entry),
            "prompts_requested": len(all_prompts),
            "prompts_completed": len([r for r in results if "error" not in r]),
        },
        "results": results,
        "correctness": checked,
        # Ties a lane-speed number to a runtime build and a tree.
        "provenance": collect_or_error(
            LLM_BASE_URL,
            SPEED_TOOL_FILES,
            start["tool_sha256"],
            host_load=start["host_load"],
            run_started_utc=start["started_utc"],
            model=model,
        ),
        "energy": energy_block(meter),
    }

    if args.output and not interrupted:
        _write_output(args.output, output, partial_path)

    if status := speed_summary.exit_code(results, interrupted=interrupted):
        sys.exit(status)


if __name__ == "__main__":
    main()
