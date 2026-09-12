#!/usr/bin/env python3
"""Benchmark any OpenAI-compatible LLM endpoint, with CPU and RAM tracking.

Named for Ollama because that is where it started; it now serves any
OpenAI-compatible server (Ollama, GenieX, llama.cpp, vLLM) selected with
--backend from backends.json. It is also the de-facto library of the suite:
the sibling tools import resolve_backend, load_backends and
detect_model_via_api from here.

Uses the Glances REST API (included in the compose stack on port 61208)
for resource monitoring, with psutil as fallback on the host.

Usage:
    orchestrant-bench speed
    orchestrant-bench speed --model gemma4:26b --prompts 10
    orchestrant-bench speed --output benchmark_results.json
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime

from orchestrant.benchmark.client import entry_config, post_json, request_headers


# LB7 — this harness is not Ollama-specific any more: it benchmarks any
# OpenAI-compatible server (Ollama, GenieX, llama.cpp, vLLM). LLM_BASE_URL is
# the name to use; OLLAMA_BASE_URL still works so existing scripts do not break.
LLM_BASE_URL = (
    os.environ.get("LLM_BASE_URL")
    or os.environ.get("OLLAMA_BASE_URL")
    or "http://localhost:11434"
)
OLLAMA_BASE_URL = LLM_BASE_URL  # backwards-compatible alias
GLANCES_URL = os.environ.get("GLANCES_URL", "http://localhost:61208")


def collect_hardware_info():
    """Collect system hardware info for reproducibility.

    LB9 — every block below used to be a bare `except: pass` over a /proc read,
    so on a non-Linux host the whole section silently produced nothing and the
    result file lost exactly the metadata that makes cross-host comparison
    meaningful. The GenieX lane runs on a WINDOWS host, so that was not
    hypothetical. Now: /proc first (richest), psutil/platform as a portable
    fallback, and an explicit `incomplete` list naming whatever is still
    missing, so a gap is visible instead of silent.
    """
    info = {}
    info["timestamp"] = datetime.now(UTC).isoformat()

    # OS info
    try:
        import platform

        info["os"] = platform.system()
        info["os_release"] = platform.release()
        info["os_version"] = platform.version()
        info["architecture"] = platform.machine()
        info["processor"] = platform.processor()
    except Exception:
        pass

    # CPU info from /proc/cpuinfo (Linux)
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
        cpus = [p for p in cpuinfo.split("\n\n") if p.strip()]
        info["cpu_count"] = len(cpus)
        for line in cpus[0].split("\n") if cpus else []:
            if "model name" in line.lower():
                info["cpu_model"] = line.split(":")[1].strip()
                break
        # Total cores per CPU (core id max + 1)
        core_ids = set()
        for cpu in cpus:
            for line in cpu.split("\n"):
                if "core id" in line.lower():
                    core_ids.add(line.split(":")[1].strip())
        if core_ids:
            info["cpu_physical_cores"] = len(core_ids)
        # Thread(s) per core
        for line in cpus[0].split("\n") if cpus else []:
            if "siblings" in line.lower():
                info["cpu_threads_per_core"] = int(line.split(":")[1].strip())
                break
        info["cpu_total_threads"] = info["cpu_count"]
    except Exception:
        pass

    # RAM
    try:
        with open("/proc/meminfo") as f:
            mem = f.read()
        for line in mem.split("\n"):
            if line.startswith("MemTotal:"):
                info["ram_total_kb"] = int(line.split()[1])
                info["ram_total_gb"] = round(info["ram_total_kb"] / (1024**2), 1)
                break
    except Exception:
        pass

    # Container info via cgroup
    try:
        with open("/proc/1/cgroup") as f:
            cgroup = f.read()
        info["in_container"] = (
            "docker" in cgroup or "containerd" in cgroup or len(cgroup.splitlines()) > 0
        )
    except Exception:
        info["in_container"] = None

    # OLLAMA_HOST env
    info["ollama_host"] = os.environ.get("OLLAMA_HOST", "default")

    # ── Portable fallback (LB9) ───────────────────────────────────────────
    # Fills whatever /proc could not provide -- on Windows and macOS that is
    # everything.
    try:
        import platform as _pf

        info.setdefault("os", _pf.system())
        info.setdefault("os_release", _pf.release())
        info.setdefault("architecture", _pf.machine())
        if not info.get("processor"):
            info["processor"] = _pf.processor() or None
    except Exception:
        pass
    try:
        import psutil as _ps

        if not info.get("cpu_physical_cores"):
            info["cpu_physical_cores"] = _ps.cpu_count(logical=False)
        if not info.get("cpu_total_threads"):
            info["cpu_total_threads"] = _ps.cpu_count(logical=True)
        if not info.get("ram_total_gb"):
            info["ram_total_gb"] = round(_ps.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass

    # Name the gaps rather than hiding them: a benchmark whose host is unknown
    # cannot be compared against another host later.
    expected = ("os", "architecture", "cpu_total_threads", "ram_total_gb")
    missing = [k for k in expected if not info.get(k)]
    if missing:
        info["incomplete"] = missing

    return info


# ── Prompts of varying length for realistic benchmarking ──────────────────────

SHORT_PROMPTS = [
    "What is 2+2?",
    "Say hello.",
    "What is the capital of France?",
    "Explain recursion in one sentence.",
    "What is an API?",
]

MEDIUM_PROMPTS = [
    "Write a Python function that computes the Fibonacci sequence using dynamic programming. Include a brief explanation of the time complexity.",
    "Explain the difference between REST and GraphQL APIs. When would you choose one over the other? Provide concrete examples.",
    "Write a bash script that monitors CPU and memory usage of a specific process every 5 seconds and logs the results to a CSV file.",
]

# ── Named backends ────────────────────────────────────────────────────────────
#
# Any OpenAI-compatible server can be benchmarked here, but the two that matter
# in this repo -- the Ollama service this stack brings up, and the Snapdragon
# GenieX lanes -- deserve names rather than URLs typed from memory. backends.json
# maps a name to a base_url and an optional default model.
#
# Resolution order, most specific first:
#   1. --base-url on the command line
#   2. LLM_BASE_URL / OLLAMA_BASE_URL in the environment
#   3. --backend <name> from backends.json
#   4. the entry backends.json marks as default (ollama)
# Env beats --backend on purpose: a wrapper script that exports the variable
# should not be silently overridden by a stale default in a config file.


def _default_backends_file():
    """Where the named-backend registry lives.

    LLM_BACKENDS wins. Otherwise the family checkout's hub file when OrchestrANT
    is developed with its submodule beside it -- the serving stack and the lanes
    it defines live in ANTfrastructure, which stays their owner. Pip installs
    have no submodule, so the last resort is a copy next to this module (absent
    by default; load_backends then returns an empty registry by design).
    """
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
    """Read backends.json. Missing or malformed -> empty registry, never raises:
    a broken config must not stop someone benchmarking an explicit URL.
    """
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
    """The whole backends.json entry behind resolve_backend's 3-tuple.

    Carries the optional api_key_env / headers / request_extra / probe fields
    that resolve_backend's (url, model, source) cannot. An explicit --base-url
    with no --backend resolves to {}: guessing an entry from a URL would attach
    someone's API key to an endpoint they typed by hand.
    """
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


# ── Correctness probes (LB1) ──────────────────────────────────────────────────
#
# Speed metrics alone cannot tell a working model from a broken one: a model
# emitting fluent nonsense scores EXCELLENT tokens/sec. This was not
# hypothetical -- GenieX's i-quant kernels produce fast garbage (v0.5.0 and
# v0.6.1 alike)
# ('\n\n\n....\n\n', ' majorityathersyre...') that every throughput metric
# rated as a good run (see docs/geniex-local-ai-setup.md).
#
# So: a handful of prompts whose answers can be CHECKED, not eyeballed. These
# are deliberately not a capability benchmark -- they are a smoke test that
# separates "the model works" from "the weights or kernels are broken", and
# secondarily shows coarse quantisation damage (measured on Qwen3-4B at
# temperature 0: Q4_0 6/6, Q2_K 4/6, i-quant 0/6 -- the 2-bit losses were both
# reasoning items, while arithmetic and factual recall survived).
#
# Each entry: (prompt, [accepted answers]). Matching is case-insensitive on
# the FINAL answer only (anything after </think> is stripped first) and
# anchored on word boundaries, so "3" does not match inside "13".

CORRECTNESS_PROBES = [
    # Deliberately a SMALL multiplication. An earlier version used 847*293,
    # which a healthy 4B could not finish inside 4000 tokens of thinking -- so
    # the probe reported INCONCLUSIVE on a perfectly good model. A check that
    # cries wolf on healthy models is a check people learn to ignore.
    ("What is 23 * 17? Reply with only the number.", ["391"]),
    ("What is the capital of Australia? Reply with only the city name.", ["canberra"]),
    (
        "How many times does the letter 'r' appear in the word strawberry? "
        "Reply with only the digit.",
        ["3"],
    ),
    (
        "If 5 machines make 5 widgets in 5 minutes, how many minutes do 100 "
        "machines need to make 100 widgets? Reply with only the number.",
        ["5"],
    ),
    ("What is 17 squared? Reply with only the number.", ["289"]),
    ("Which number is larger, 9.11 or 9.9? Reply with only the number.", ["9.9"]),
]


LONG_PROMPTS = [
    """Write a detailed technical blog post about building a production-ready LLM inference server. Cover the following aspects:
1. Model serving frameworks and their trade-offs (Ollama, vLLM, TGI, Triton Inference Server)
2. Hardware considerations: CPU vs GPU inference, memory requirements, quantization
3. API design: OpenAI-compatible endpoints, streaming, batching
4. Monitoring and observability: metrics to track, logging, alerting
5. Deployment strategies: Docker Compose, Kubernetes, auto-scaling
6. Security considerations: rate limiting, authentication, input sanitization

For each section, provide practical recommendations based on real-world production experience.""",
]


def get_glances_data(endpoint):
    """Fetch JSON data from the Glances REST API (v4, falling back to v3).

    Glances 4 (the latest-full image) serves /api/4 and dropped /api/3, so
    probe v4 first and fall back to v3 for older Glances containers.
    """
    import requests

    for api_ver in ("4", "3"):
        try:
            r = requests.get(f"{GLANCES_URL}/api/{api_ver}/{endpoint}", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None


def top_cpu_processes(limit=3):
    """Busiest processes since the PREVIOUS call to this function (LB8).

    psutil's per-process cpu_percent(None) reports usage since that process
    was last polled, so calling this before and after a request makes the
    second call a real measurement of who burned CPU *during* it.

    Why it is worth reporting: the process that owns the serving port is not
    necessarily the one doing the work. GenieX spawns a separate worker, and
    sampling the port owner showed 11 % of 800 % while the actual worker sat
    at 752 % -- i.e. it looked idle while it was pinning 7.5 of 8 cores.
    Naming the busiest process removes that whole class of mistake.
    """
    try:
        import psutil
    except Exception:
        return []
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            pct = p.cpu_percent(None)
            if pct > 0:
                procs.append(
                    {
                        "pid": p.pid,
                        "name": p.info.get("name") or "?",
                        "cpu_percent": round(pct, 1),
                    }
                )
        except Exception:
            continue
    procs.sort(key=lambda d: d["cpu_percent"], reverse=True)
    return procs[:limit]


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


def _get_json(url, entry=None, timeout=5):
    """GET one JSON document with the backend entry's auth and headers."""
    req = urllib.request.Request(url, headers=request_headers(entry))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def detect_model_via_api(base_url=None, entry=None):
    """Detect a served model.

    Asks the portable OpenAI endpoint (/v1/models) FIRST. The previous version
    probed Ollama's /api/show with a hardcoded "gemma4:26b" and returned that
    name on any 200 -- which reported the wrong model on any host serving
    something else, and nothing at all on a non-Ollama server.

    `entry` carries the auth a hosted endpoint needs; a backend marked
    probe:false is never asked at all (see main()).
    """
    base = base_url or LLM_BASE_URL
    try:
        models = _get_json(f"{base}/v1/models", entry).get("data", [])
        if models:
            return models[0]["id"]
    except Exception:
        pass
    # Ollama-native fallback: /api/tags lists what is actually pulled.
    try:
        tags = _get_json(f"{base}/api/tags", entry).get("models", [])
        if tags:
            return tags[0].get("name") or tags[0].get("model", "unknown")
    except Exception:
        pass
    return "unknown"


def resolve_model(explicit, backend_model, entry, detect=None):
    """--model, else the backend's default, else ask the endpoint.

    A backend marked probe:false is never asked -- on a paid host a discovery
    request costs money and may not exist. Its id has to be named, and saying
    so beats an unexplained 404 halfway through a run.
    """
    if explicit or backend_model:
        return explicit or backend_model
    if not (entry or {}).get("probe", True):
        raise SystemExit(
            "this backend is marked probe:false, so /v1/models is not queried. "
            'Pass --model, or give the entry a "model" field in backends.json.'
        )
    return (detect or detect_model_via_api)(entry=entry)


def benchmark_chat(
    model,
    prompts,
    *,
    max_tokens=256,
    temperature=0.0,
    stream=False,
    extra_params=None,
    sample_interval=0.2,
    warmup=True,
    base_url=None,
    entry=None,
):
    """Run a benchmark against the OpenAI-compatible chat completions endpoint.

    Yields dicts with timing and resource data for each prompt. `entry` is the
    backends.json entry: its api_key_env / headers / request_extra travel with
    every request through orchestrant.benchmark.client.post_json.
    """
    endpoint = f"{base_url or LLM_BASE_URL}/v1/chat/completions"

    # Warmup: one short request to load the model into memory
    if warmup:
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
        except Exception:
            pass
        time.sleep(1)

    for i, prompt in enumerate(prompts):
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

        # Sample resources before request
        resources_before = sample_resources()
        top_cpu_processes()  # LB8: prime the per-process counters
        start = time.monotonic()
        first_token_at = None  # LB2: set on the first content-bearing chunk
        streamed_chunks = 0  # fallback when a server omits the usage chunk

        try:
            if stream:
                content_chunks = []
                usage = None
                with post_json(
                    endpoint, payload, entry=entry, stream=True, timeout=300
                ) as response:
                    for line in response.lines():
                        # The space after "data:" is OPTIONAL in SSE: GenieX
                        # omits it, and "data: " parsed nothing against it.
                        if line.startswith("data:"):
                            data = line[5:].lstrip()
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                # Usage in final streaming chunk (choices may be empty)
                                if "usage" in chunk:
                                    usage = chunk["usage"]
                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                piece = delta.get("content", "") or delta.get(
                                    "reasoning", ""
                                )
                                if piece:
                                    streamed_chunks += 1
                                # LB2: first token carrying actual content marks the
                                # end of prefill. Empty role-only deltas do not count.
                                if piece and first_token_at is None:
                                    first_token_at = time.monotonic()
                                content_chunks.append(piece)
                            except json.JSONDecodeError:
                                pass
                content = "".join(content_chunks)
            else:
                with post_json(endpoint, payload, entry=entry, timeout=300) as r:
                    body = r.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage")
        except Exception as e:
            yield {
                "prompt_index": i,
                "prompt_preview": prompt[:60],
                "error": str(e),
                "latency_s": time.monotonic() - start,
            }
            continue

        elapsed = time.monotonic() - start
        resources_after = sample_resources()
        # LB8: who actually did the work during this request?
        busiest = top_cpu_processes()

        # Average resources during request
        avg_cpu = (resources_before["cpu_percent"] + resources_after["cpu_percent"]) / 2
        avg_ram_gb = (
            resources_before["ram_used_gb"] + resources_after["ram_used_gb"]
        ) / 2

        prompt_tokens = usage.get("prompt_tokens", 0) if usage else 0
        completion_tokens = usage.get("completion_tokens", 0) if usage else 0
        total_tokens = usage.get("total_tokens", 0) if usage else 0

        # Not every OpenAI-compatible server honours stream_options.include_usage
        # (GenieX does not). Without a fallback the whole run reports 0 tok/s,
        # which reads as "catastrophically slow" rather than "not reported".
        # Counting content deltas is an approximation -- flagged as such.
        tokens_estimated = False
        if not completion_tokens and streamed_chunks:
            completion_tokens = streamed_chunks
            total_tokens = total_tokens or (prompt_tokens + completion_tokens)
            tokens_estimated = True

        tokens_per_sec = completion_tokens / elapsed if elapsed > 0 else 0.0

        # LB2 — prefill vs decode. `tokens_per_sec` above mixes both: it divides
        # by the WHOLE request, so a slow prefill silently depresses what looks
        # like a decode rate. Split them, because for an agent the wait is
        # dominated by prefill (measured: 13.1 s TTFT on a 2.5k-token prompt).
        ttft = (first_token_at - start) if first_token_at is not None else None
        decode_tps = None
        prefill_tps = None
        if ttft is not None:
            decode_window = elapsed - ttft
            if decode_window > 0 and completion_tokens > 1:
                # The first token is produced BY the prefill, so the decode
                # window covers completion_tokens - 1.
                decode_tps = (completion_tokens - 1) / decode_window
            if ttft > 0 and prompt_tokens:
                prefill_tps = prompt_tokens / ttft

        # LB3 — a reasoning model can be the fastest per token and the slowest
        # to a usable answer (measured: Qwen3-1.7B 31.7 tok/s but 1921 tokens =
        # 60.8 s, vs a 4B-Instruct at 19.5 tok/s and 26.8 s). Record how much of
        # the output was thinking so the ranking metric can be understood.
        # Character-based on purpose: per-segment token counts are not exposed.
        answer = content.split("</think>")[-1] if "</think>" in content else content
        thinking_char_share = (
            round(1 - len(answer) / len(content), 3) if content else None
        )

        yield {
            "prompt_index": i,
            "prompt_preview": prompt[:60],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "tokens_estimated": tokens_estimated,
            "tokens_per_sec": round(tokens_per_sec, 2),
            # LB3: wall time to a FINISHED answer -- the metric to rank by.
            # Same measurement as latency_s, named for what it means.
            "wall_s_to_answer": round(elapsed, 2),
            "latency_s": round(elapsed, 2),
            "ttft_s": round(ttft, 3) if ttft is not None else None,
            "decode_tok_per_sec": round(decode_tps, 2) if decode_tps else None,
            "prefill_tok_per_sec": round(prefill_tps, 1) if prefill_tps else None,
            "thinking_char_share": thinking_char_share,
            "cpu_percent": round(avg_cpu, 1),
            "ram_used_gb": round(avg_ram_gb, 2),
            "top_processes": busiest,
            "content_preview": content[:80],
        }


def _answer_matches(content, accepted):
    """Does the model's FINAL answer contain one of the accepted strings?

    Two deliberate choices, both learned from a probe that scored false
    positives: strip any <think> block first (a reasoning model often states
    and then discards a wrong intermediate value), and anchor on word
    boundaries so "3" does not match inside "13" or "0.31".
    """
    import re

    # A reasoning model that never CLOSED its <think> block ran out of budget
    # before answering. Searching the thinking text would score a discarded
    # intermediate value as a correct answer -- the exact false positive this
    # function exists to prevent. No final answer means not correct.
    if "<think>" in content and "</think>" not in content:
        return False

    answer = content.split("</think>")[-1] if "</think>" in content else content
    # Bias to the end: the final answer is what counts, not a mid-stream aside.
    answer = answer[-400:].lower().replace(",", "").replace("*", "")
    for exp in accepted:
        # Trailing rule: a sentence-ending "." must NOT break the match
        # ("248,171." -> "248171."), but ".<digit>" must, so "3" does not
        # match inside "3.5". Leading rule blocks "13" and "0.31".
        if re.search(rf"(?<![\w.]){re.escape(exp.lower())}(?!\w)(?!\.\d)", answer):
            return True
    return False


def run_correctness_probe(
    model, *, max_tokens=4000, extra_params=None, base_url=None, entry=None
):
    """LB1 — check the model still answers correctly, not just quickly.

    Returns a dict with the score and per-item detail, or None if the endpoint
    could not be reached at all. Always runs at temperature 0: this is a
    regression check, not a creativity test.

    max_tokens defaults high because reasoning models spend most of their
    budget inside <think>. A model cut off before it answers scores WRONG --
    deliberately, since a truncated run is not a correct one -- so too small a
    budget misreports a healthy model. Measured: Qwen3-4B scores 5/6 at 900
    (arithmetic truncated) and 6/6 at 2500.
    """
    endpoint = f"{base_url or LLM_BASE_URL}/v1/chat/completions"
    items = []

    for prompt, accepted in CORRECTNESS_PROBES:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if extra_params:
            payload.update(extra_params)
        try:
            with post_json(endpoint, payload, entry=entry, timeout=600) as r:
                content = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            items.append(
                {
                    "prompt": prompt[:60],
                    "expected": accepted[0],
                    "error": str(e)[:120],
                    "correct": False,
                }
            )
            continue
        truncated = "<think>" in content and "</think>" not in content
        answer = content.split("</think>")[-1].strip()
        items.append(
            {
                "prompt": prompt[:60],
                "expected": accepted[0],
                "answer_preview": (
                    "<truncated inside <think>, raise --correctness-max-tokens>"
                    if truncated
                    else answer[:80]
                ),
                "truncated": truncated,
                "correct": _answer_matches(content, accepted),
            }
        )

    scored = [i for i in items if "error" not in i]
    if not scored:
        return None

    # Truncation is NOT incorrectness. A model cut off mid-thought did not
    # answer wrongly -- we failed to measure it. Conflating the two makes a
    # healthy model look degraded, which is the fastest way to teach someone
    # to ignore this check. Count them apart and say which happened.
    truncated = sum(1 for i in items if i.get("truncated"))
    wrong = sum(
        1
        for i in items
        if not i["correct"] and not i.get("truncated") and "error" not in i
    )
    return {
        "score": sum(1 for i in items if i["correct"]),
        "total": len(items),
        "wrong": wrong,
        "truncated": truncated,
        "errors": len(items) - len(scored),
        "items": items,
    }


_sampler_warned = False


def sample_resources():
    """Unified resource sampler: Glances API → psutil → zeros.

    Never raises: a broken sampler (missing psutil, flaky Glances, transient
    psutil read error) must not abort a multi-hour benchmark run. Failures
    degrade to zero readings with a single warning for the whole run.
    """
    global _sampler_warned
    try:
        result = sample_resources_glances()
        if result is not None:
            return result
        return sample_resources_psutil()
    except Exception as e:
        if not _sampler_warned:
            _sampler_warned = True
            print(
                f"  WARNING: resource sampling failed ({e!r}); "
                "reporting zeros for CPU/RAM from now on",
                file=sys.stderr,
            )
        return {
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
        }


def print_separator(char="━", width=80):
    print(char * width)


def print_table(results):
    """Print benchmark results as a formatted table."""
    if not results:
        print("No results to display.")
        return

    headers = ["#", "Prompt", "PT", "CT", "T/s", "TTFT", "Ans(s)", "CPU%", "RAM(GB)"]
    col_widths = [3, 38, 5, 5, 7, 7, 8, 7, 8]

    def fmt_row(values):
        return "  ".join(f"{str(v)[:w]:{w}}{'':>1}" for v, w in zip(values, col_widths))

    print_separator("─")
    print(fmt_row(headers))
    print_separator("─")

    for r in results:
        if "error" in r:
            print(
                f"  {r['prompt_index']:<3}  {r['prompt_preview'][:38]:<38}  ERROR: {r['error'][:40]}"
            )
            continue
        ttft = r.get("ttft_s")
        print(
            fmt_row(
                [
                    r["prompt_index"],
                    r["prompt_preview"][:38],
                    r.get("prompt_tokens", "-"),
                    r.get("completion_tokens", "-"),
                    r.get("tokens_per_sec", "-"),
                    f"{ttft:.2f}" if ttft is not None else "-",
                    r.get("wall_s_to_answer", r.get("latency_s", "-")),
                    r.get("cpu_percent", "-"),
                    r.get("ram_used_gb", "-"),
                ]
            )
        )

    print_separator("─")
    print()

    latencies = [
        r["latency_s"] for r in results if "latency_s" in r and "error" not in r
    ]
    tps_vals = [
        r["tokens_per_sec"]
        for r in results
        if "tokens_per_sec" in r and "error" not in r
    ]
    cpu_vals = [
        r["cpu_percent"] for r in results if "cpu_percent" in r and "error" not in r
    ]
    ram_vals = [
        r["ram_used_gb"] for r in results if "ram_used_gb" in r and "error" not in r
    ]
    comp_tokens = [
        r["completion_tokens"]
        for r in results
        if "completion_tokens" in r and "error" not in r
    ]
    total_tokens = [
        r["total_tokens"] for r in results if "total_tokens" in r and "error" not in r
    ]

    if latencies:
        print(f"  Summary ({len(latencies)} requests):")
        print(
            f"    Latency:        {min(latencies):.2f}s  /  {sum(latencies) / len(latencies):.2f}s avg  /  {max(latencies):.2f}s max"
        )
        print(
            f"    Tokens/sec:     {min(tps_vals):.1f}  /  {sum(tps_vals) / len(tps_vals):.1f} avg  /  {max(tps_vals):.1f} max"
        )
        print(
            f"    CPU:            {min(cpu_vals):.1f}%  /  {sum(cpu_vals) / len(cpu_vals):.1f}% avg  /  {max(cpu_vals):.1f}% max"
        )
        print(
            f"    RAM used:       {min(ram_vals):.2f}GB  /  {sum(ram_vals) / len(ram_vals):.2f}GB avg  /  {max(ram_vals):.2f}GB max"
        )
        if comp_tokens and total_tokens:
            print(
                f"    Completion tok: {sum(comp_tokens)} total  /  {sum(comp_tokens) / len(comp_tokens):.1f} avg per req"
            )
            total_elapsed = sum(latencies)
            print(
                f"    Overall:        {sum(total_tokens)} tokens in {total_elapsed:.1f}s  =  {sum(total_tokens) / total_elapsed:.1f} tok/s"
            )

        # LB2 — prefill is usually what the user actually waits on.
        ttfts = [r["ttft_s"] for r in results if r.get("ttft_s") is not None]
        if ttfts:
            print(
                f"    TTFT:           {min(ttfts):.2f}s  /  {sum(ttfts) / len(ttfts):.2f}s avg  /  {max(ttfts):.2f}s max"
            )
            decs = [
                r["decode_tok_per_sec"] for r in results if r.get("decode_tok_per_sec")
            ]
            pres = [
                r["prefill_tok_per_sec"]
                for r in results
                if r.get("prefill_tok_per_sec")
            ]
            if decs:
                print(
                    f"    Decode only:    {sum(decs) / len(decs):.1f} tok/s avg  (excludes prefill)"
                )
            if pres:
                print(f"    Prefill:        {sum(pres) / len(pres):.0f} tok/s avg")
        elif not any("error" in r for r in results):
            print("    TTFT:           not measured — re-run with --stream")

        # LB8 — name the process that actually burned CPU. On this host the
        # serving process and the inference worker are different PIDs.
        busiest = {}
        for r in results:
            for proc in r.get("top_processes") or []:
                key = f"{proc['name']} (pid {proc['pid']})"
                busiest[key] = max(busiest.get(key, 0), proc["cpu_percent"])
        if busiest:
            top = sorted(busiest.items(), key=lambda kv: kv[1], reverse=True)[:2]
            summary = ",  ".join(f"{name} {pct:.0f}%" for name, pct in top)
            print(f"    Busiest proc:   {summary}")

        # LB3 — rank by time to a finished answer, not by tok/s.
        answers = [r["wall_s_to_answer"] for r in results if r.get("wall_s_to_answer")]
        if answers:
            print(
                f"    Time to answer: {sum(answers) / len(answers):.1f}s avg  <-- rank models by THIS, not tok/s"
            )
        shares = [
            r["thinking_char_share"] for r in results if r.get("thinking_char_share")
        ]
        if shares:
            print(
                f"    Thinking share: {100 * sum(shares) / len(shares):.0f}% of output was <think> "
                "(pure latency for an agent)"
            )


def print_correctness(probe):
    """Render the LB1 probe. A model can be fast and wrong; show both."""
    print()
    if probe is None:
        print("  Correctness probe: NO RESULT — endpoint unreachable")
        return
    score, total = probe["score"], probe["total"]
    wrong, truncated = probe.get("wrong", 0), probe.get("truncated", 0)
    if wrong == 0 and truncated == 0:
        verdict = "OK"
    elif wrong == 0:
        verdict = "INCONCLUSIVE"  # only cut off, never actually wrong
    elif wrong >= total / 2:
        verdict = "BROKEN"
    else:
        verdict = "DEGRADED"
    extra = f", {truncated} truncated" if truncated else ""
    print(f"  Correctness probe: {score}/{total} correct{extra}  [{verdict}]")
    for item in probe["items"]:
        if "error" in item:
            print(f"    ERR  {item['prompt'][:52]:<52} {item['error'][:40]}")
            continue
        mark = "ok " if item["correct"] else "XX "
        print(
            f"    {mark}  expected={item['expected']:<9} got={item.get('answer_preview', '')[:44]!r}"
        )
    if truncated:
        print(
            f"    NOTE: {truncated} probe(s) ran out of tokens before answering. That is a"
        )
        print(
            "          MEASUREMENT limit, not a model fault — raise --correctness-max-tokens."
        )
    if wrong:
        print(
            "    NOTE: wrong answers here usually mean broken kernels or an over-aggressive"
        )
        print(
            "          quant, not a slow model. Check the GGUF tensor types before tuning speed."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark any OpenAI-compatible LLM endpoint (--backend selects one)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--model", default=None, help="Model name (auto-detected if omitted)"
    )
    parser.add_argument(
        "--prompts", type=int, default=0, help="Number of prompts (0 = all)"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=256, help="Max tokens per response"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="Sampling temperature"
    )
    parser.add_argument("--stream", action="store_true", help="Use streaming responses")
    parser.add_argument(
        "--output", default=None, help="Write results as JSON to this file"
    )
    parser.add_argument("--no-warmup", action="store_true", help="Skip warmup request")
    parser.add_argument(
        "--extra-params",
        default=None,
        help='Extra JSON params for the request body (e.g. \'{"num_ctx":16000,"repeat_penalty":1.1}\')',
    )
    parser.add_argument(
        "--load", default=None, help="Load and re-display results from a JSON file"
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="Named backend from backends.json (e.g. ollama, geniex-npu). "
        "Supplies the base URL and a default model.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Explicit base URL; overrides --backend and the environment",
    )
    parser.add_argument(
        "--list-backends",
        action="store_true",
        help="Show the configured backends and exit",
    )
    parser.add_argument(
        "--correctness",
        action="store_true",
        help="LB1: also run the verifiable-answer probe (catches a model that is "
        "fast but broken — speed metrics cannot)",
    )
    parser.add_argument(
        "--correctness-only",
        action="store_true",
        help="Run ONLY the correctness probe and exit (quick health check)",
    )
    parser.add_argument(
        "--correctness-max-tokens",
        type=int,
        default=4000,
        help="Token budget per probe. Must be generous: a reasoning model "
        "truncated mid-thought scores WRONG by design (a truncated run is "
        "not a correct one). Measured: Qwen3-4B needs >900 for arithmetic "
        "(default 4000)",
    )

    args = parser.parse_args()

    if args.list_backends:
        print_backends()
        return

    # Resolve the endpoint before anything talks to it. Rebinding the module
    # global keeps every existing call site working unchanged.
    global LLM_BASE_URL, OLLAMA_BASE_URL
    LLM_BASE_URL, backend_model, source = resolve_backend(args.backend, args.base_url)
    OLLAMA_BASE_URL = LLM_BASE_URL
    entry = resolve_backend_entry(args.backend, args.base_url)

    if args.load:
        with open(args.load) as f:
            data = json.load(f)
        print(f"\n  Loaded benchmark results from {args.load}")
        print(
            f"  Model: {data.get('model', '?')}  |  Date: {data.get('timestamp', '?')}"
        )
        print()
        print_table(data.get("results", []))
        return

    # A backend may name its own default model; an explicit --model still wins.
    model = resolve_model(args.model, backend_model, entry)

    if args.correctness_only:
        print(f"\n  Model: {model}")
        print(f"  API:   {LLM_BASE_URL}/v1  (from {source})")
        probe = run_correctness_probe(
            model,
            max_tokens=args.correctness_max_tokens,
            extra_params=json.loads(args.extra_params) if args.extra_params else None,
            entry=entry,
        )
        print_correctness(probe)
        # Distinct exit codes so a gate can tell "model is wrong" (act on it)
        # from "we cut it off" (re-run with a bigger budget).
        if probe is None:
            sys.exit(1)
        if probe.get("wrong"):
            sys.exit(1)
        sys.exit(2 if probe.get("truncated") else 0)

    print(f"\n  Model: {model}")
    print(f"  API:   {LLM_BASE_URL}/v1  (from {source})")
    print(f"  Glances: {GLANCES_URL}/api/4 (v3 fallback)")
    print()

    # Build the prompt list
    all_prompts = SHORT_PROMPTS + MEDIUM_PROMPTS + LONG_PROMPTS
    if args.prompts > 0:
        all_prompts = all_prompts[: args.prompts]

    print(
        f"  Running {len(all_prompts)} benchmark prompts (max_tokens={args.max_tokens}, stream={args.stream})"
    )
    print()

    extra_params = json.loads(args.extra_params) if args.extra_params else None

    # Incremental persistence: every completed result is appended to a JSONL
    # side file so a crash or Ctrl-C never discards finished measurements.
    # The side file uses a .jsonl suffix on purpose — run_benchmarks.sh and
    # the viewer manifest only glob *.json, so it can never be mistaken for
    # a finished result file.
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

    print_table(results)

    correctness = None
    if args.correctness and not interrupted:
        correctness = run_correctness_probe(
            model,
            max_tokens=args.correctness_max_tokens,
            extra_params=extra_params,
            entry=entry,
        )
        print_correctness(correctness)

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
            # What the backend entry added to every request. Names only for the
            # header and key fields -- a value could be the key itself.
            "backend_entry": entry_config(entry),
            "prompts_requested": len(all_prompts),
            "prompts_completed": len([r for r in results if "error" not in r]),
        },
        "results": results,
        "correctness": correctness,
    }

    if args.output and not interrupted:
        # Atomic write: never leave a truncated/half-written JSON behind for
        # run_benchmarks.sh's summary loop or the viewer manifest to choke on.
        tmp_path = f"{args.output}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(output, f, indent=2)
        os.replace(tmp_path, args.output)
        if partial_path and os.path.exists(partial_path):
            os.remove(partial_path)
        print(f"\n  Results written to {args.output}")

    if interrupted:
        sys.exit(130)


if __name__ == "__main__":
    main()
