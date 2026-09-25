"""Does the server still behave the way the tooling assumes?

Every GenieX release has moved something a benchmark here depended on, and
each move was found by hand, usually after it had already distorted a number:
v0.5 -> v0.6 dropped the 2048-token output cap, started honouring
max_tokens, added tool-call parsing and a prefix cache; v0.7 added host-side
stop sequences for QAIRT bundles (on /v1/completions only — chat still ignores
`stop`), a per-request `power_mode`, and a default system prompt read from
bundle metadata. The GenieX page's § 1n table was built one curl at a time.

`orchestrant-bench contract` re-asks those questions — the output cap
(`output_cap`: a 3000-token budget on a reply that runs long) and the
bundle's default system prompt (`bundle_system_prompt`) included — plus
whether a `response_format` JSON schema is honoured, ignored or refused. It
takes a few minutes, most of them the output cap's long reply (several on a
CPU lane), and writes a report in the shared envelope, so two runtimes can be
diffed (`--diff old.json new.json`) instead of rediscovered. Every check
records a neutral answer — `yes`, `no`, `inconclusive`, `error` or `skipped`
— plus the evidence. None of them is a pass or a fail: a lane that ignores
`seed` is not broken, but a benchmark that assumed otherwise would be.

    orchestrant-bench contract --backend geniex-npu --overflow-tokens 6000 --output npu.json
    orchestrant-bench contract --diff v061-npu.json v070-npu.json
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import random
import re
import sys
import time
import urllib.error

from orchestrant.benchmark.answers import split_answer
from orchestrant.benchmark.client import post_json


# A fixed vocabulary and seed: the same filler on every run and every
# version, so a changed prefill time is the server, not the prompt.
_WORDS = (
    "lane", "server", "model", "token", "cache", "prefill", "decode", "window",
    "budget", "request", "answer", "context", "kernel", "thread", "cluster",
    "memory", "report", "runtime", "version", "grader", "sample", "repeat",
    "fixture", "harness", "verdict", "interval", "baseline",
)  # fmt: skip


def filler(approx_tokens, seed=1234):
    """Deterministic prose of roughly `approx_tokens` tokens (usage has the real count)."""
    rng = random.Random(seed)  # nosec B311  # noqa: S311 -- filler text, not a secret
    words, sentences = int(approx_tokens / 1.3), []
    while words > 0:
        n = min(words, rng.randint(8, 16))
        sentences.append(
            " ".join(rng.choice(_WORDS) for _ in range(n)).capitalize() + "."
        )
        words -= n
    return " ".join(sentences)


# Each request's timeout when --timeout is not given.
DEFAULT_TIMEOUT_S = 600
# The slowest cold prefill measured, halved. 2026-09-24-roadmap: GenieX's CPU
# lane prefilled the 9B at 61.7-66.6 tok/s, Ollama at 4 threads at 11.9 (4487
# tokens) and 11.7 (7159) -- whose 7175-token prefix then timed out at 600 s.
# Half, for a rate that falls with depth: the thinking 4B's fell from 87.5 to
# 49.2 tok/s between 4.5k and 10.7k tokens.
SLOWEST_PREFILL_TOK_S = 6


def prompt_timeout(tokens, timeout=None):
    """Seconds a request carrying a `tokens`-long prompt may take.

    --timeout (DEFAULT_TIMEOUT_S unless given), or the prompt's prefill at
    SLOWEST_PREFILL_TOK_S where that is longer: 1334 s at 8000 prefix tokens,
    and the 2000-token default keeps 600.
    """
    return max(timeout or DEFAULT_TIMEOUT_S, math.ceil(tokens / SLOWEST_PREFILL_TOK_S))


def _timeout(ctx):
    return ctx.get("timeout") or DEFAULT_TIMEOUT_S


def _chat(ctx, messages, timeout=None, **params):
    """One non-streamed chat call -> (seconds, body or None, error or None)."""
    body = {"model": ctx["model"], "messages": messages, "stream": False, **params}
    t0 = time.monotonic()
    try:
        with post_json(
            f"{ctx['base_url']}/v1/chat/completions",
            body,
            entry=ctx["entry"],
            timeout=timeout or _timeout(ctx),
        ) as r:
            return time.monotonic() - t0, r.json(), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return time.monotonic() - t0, None, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return time.monotonic() - t0, None, f"{type(e).__name__}: {e}"[:300]


def _content(body):
    msg = ((body or {}).get("choices") or [{}])[0].get("message") or {}
    return msg.get("content") or ""


def _finish(body):
    return ((body or {}).get("choices") or [{}])[0].get("finish_reason")


def _usage(body):
    return (body or {}).get("usage") or {}


def _user(text):
    return [{"role": "user", "content": text}]


def check_max_tokens(ctx):
    _, body, err = _chat(
        ctx,
        _user("Count from 1 to 200, separated by spaces."),
        max_tokens=16,
        temperature=0,
    )
    if err:
        return {"answer": "error", "evidence": err}
    used = _usage(body).get("completion_tokens")
    honoured = used is not None and used <= 16 and _finish(body) == "length"
    return {
        "answer": "yes" if honoured else "no",
        "evidence": f"max_tokens=16 -> completion_tokens={used}, finish_reason={_finish(body)!r}",
    }


# The budget the output-cap check asks for, and a reply that runs past it.
_CAP_ASK = 3000
_CAP_PROMPT = (
    "Write every whole number from 1 to 5000 in order, one per line. "
    "Output only the numbers, with no other text."
)
# A server may stop at one of these and still report a normal finish.
_ROUND_CAPS = (256, 512, 1024, 2048)
# Servers count the last token or two differently; this close is the budget.
_CAP_SLACK = 16


def check_output_cap(ctx):
    """Does the server stop EARLIER than max_tokens asks -- and where?

    GenieX v0.5.0's serve default cut every reply at 2048 tokens whatever the
    request asked, and nobody recorded it: two capability verdicts (roadmap
    P4.1, P4.2) are still conditional on it. `max_tokens_honoured` cannot see
    a cap -- it asks for 16. Here the budget is 3000 on a reply that runs
    longer: ending short with finish_reason "length", or at exactly a round
    number reported as a finish, is the server stopping it, and
    `stopped_at_tokens` says where (prompt_tokens beside it shows when the
    ceiling is the lane's context rather than a cap). A reply that simply
    finished is inconclusive: the model stopped before any cap could show.
    """
    seconds, body, err = _chat(
        ctx,
        _user(_CAP_PROMPT),
        timeout=max(1800, _timeout(ctx)),
        max_tokens=_CAP_ASK,
        temperature=0,
    )
    if err:
        return {"answer": "error", "evidence": err}
    used, finish = _usage(body).get("completion_tokens"), _finish(body)
    evidence = (
        f"max_tokens={_CAP_ASK} -> completion_tokens={used}, "
        f"finish_reason={finish!r}, prompt_tokens={_usage(body).get('prompt_tokens')}, "
        f"{seconds:.0f}s"
    )
    if not isinstance(used, int):
        answer = "inconclusive"
        evidence += " (no completion_tokens: the stop point cannot be read)"
    elif used >= _CAP_ASK - _CAP_SLACK:
        answer = "no"
        if used > _CAP_ASK + _CAP_SLACK:
            evidence += " (MORE than asked: max_tokens itself is not honoured)"
    elif finish == "length" or used in _ROUND_CAPS:
        answer = "yes"
    else:
        answer = "inconclusive"
        evidence += " (the reply finished on its own, before a cap could show)"
    return {"answer": answer, "evidence": evidence, "stopped_at_tokens": used}


def check_usage(ctx):
    _, body, err = _chat(
        ctx, _user("Reply with the single word: ok"), max_tokens=8, temperature=0
    )
    if err:
        return {"answer": "error", "evidence": err}
    u = _usage(body)
    ok = bool(u.get("prompt_tokens")) and u.get("completion_tokens") is not None
    return {"answer": "yes" if ok else "no", "evidence": f"usage={u}"}


def _unique(text):
    """The same request text with a nonce, so no cache has seen it."""
    return f"{text} ({random.SystemRandom().randrange(10**9)})"


def check_cached_prompt_tokens(ctx):
    """Is usage.prompt_tokens still the prompt's size when the prompt is cached?

    OpenAI semantics: yes, with the hit in prompt_tokens_details.cached_tokens.
    The v0.6.1 llama.cpp lane reports 0 for a repeat and 0 cached, so a
    benchmark reading prompt size from usage sees the cache, not the prompt.
    """
    text, seen = _unique("Reply with the single word: ok"), []
    for _ in range(2):
        _, body, err = _chat(ctx, _user(text), max_tokens=4, temperature=0)
        if err:
            return {"answer": "error", "evidence": err}
        usage = _usage(body)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        seen.append((usage.get("prompt_tokens"), cached))
    (first, _), (second, cached) = seen
    return {
        "answer": "yes" if first and second == first else "no",
        "evidence": f"identical prompt: prompt_tokens {first} then {second} (cached_tokens {cached})",
    }


def check_stream_usage(ctx):
    body = {
        "model": ctx["model"],
        "messages": _user(_unique("Reply with the single word: ok")),
        "max_tokens": 8,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    usage, chunks = None, 0
    try:
        with post_json(
            f"{ctx['base_url']}/v1/chat/completions",
            body,
            entry=ctx["entry"],
            stream=True,
            timeout=_timeout(ctx),
        ) as r:
            for line in r.lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                chunks += 1
                usage = chunk.get("usage") or usage
    except Exception as e:
        return {"answer": "error", "evidence": f"{type(e).__name__}: {e}"[:300]}
    # prompt_tokens must be real (a unique prompt, so no cache hit can zero it):
    # without it a streamed prefill rate is silently null.
    usable = (
        bool(usage)
        and bool(usage.get("prompt_tokens"))
        and bool(usage.get("completion_tokens"))
    )
    return {
        "answer": "yes" if usable else "no",
        "evidence": f"{chunks} chunks, usage chunk: {usage}",
    }


_SEA = "Write one sentence about the sea."
# Near-greedy on any server: top_k 1 where it is honoured, a vanishing
# temperature where it is not. NOT temperature 0 -- see temperature0_is_greedy.
_GREEDY = {"temperature": 0.01, "top_k": 1}


def _spacer(ctx):
    """An unrelated request, so the next one is not an identical follow-up."""
    _chat(ctx, _user("Reply with the single word: ok"), max_tokens=1)


def _twice(ctx, spaced=True, **params):
    """The same request twice -> ([text, text], [prompt_tokens, ...], error).

    `spaced` puts an unrelated request between the two, and is the default:
    GenieX answers an identical request sent twice in a row along a cache path
    that changes the reply (measured on both lanes, v0.6.1 and v0.7.0), so a
    back-to-back pair measures that path, not the sampler. The FIRST draw is
    spaced too: the previous check ended on this very prompt.
    """
    replies, prompt_tokens = [], []
    for i in range(2):
        if spaced or i == 0:
            _spacer(ctx)
        _, body, err = _chat(ctx, _user(_SEA), **params)
        if err:
            return None, None, err
        replies.append(_content(body))
        prompt_tokens.append(_usage(body).get("prompt_tokens"))
    return replies, prompt_tokens, None


def _agree(ctx, label, spaced=True, **params):
    replies, _, err = _twice(ctx, spaced=spaced, **params)
    if err:
        return {"answer": "error", "evidence": err}
    same = replies[0] == replies[1]
    return {
        "answer": "yes" if same else "no",
        "evidence": f"identical ({label})"
        if same
        else f"{replies[0][:60]!r} vs {replies[1][:60]!r}",
    }


def check_temperature0(ctx):
    return _agree(ctx, "T=0, another request between", max_tokens=48, temperature=0)


def check_greedy(ctx):
    return _agree(ctx, "temperature 0.01, top_k 1", max_tokens=48, **_GREEDY)


def check_temperature0_is_greedy(ctx):
    """Is temperature 0 greedy decoding, or read as unset?

    GenieX v0.7.0 (both lanes, 2026-09-24) treats 0 as unset -- a Go zero
    value -- and samples with the default sampler; 0.01 is honoured, and so is
    top_k 1. A client that sends 0 for a reproducible run gets sampling.
    """
    _spacer(ctx)
    _, t0, err = _chat(ctx, _user(_SEA), max_tokens=48, temperature=0)
    if err:
        return {"answer": "error", "evidence": err}
    _spacer(ctx)
    _, greedy, err = _chat(ctx, _user(_SEA), max_tokens=48, **_GREEDY)
    if err:
        return {"answer": "error", "evidence": err}
    a, b = _content(t0), _content(greedy)
    return {
        "answer": "yes" if a == b else "no",
        "evidence": "T=0 reply == temperature 0.01/top_k 1 reply"
        if a == b
        else f"T=0 {a[:50]!r} vs greedy {b[:50]!r}",
    }


def check_identical_repeat(ctx):
    """Does an identical request sent twice IN A ROW get the same reply?

    Asked at near-greedy settings, after checking they reproduce with another
    request between, so a "no" is the repeat path and not the sampler. GenieX
    v0.7.0: no on both lanes -- llama.cpp prefills 0 tokens and samples the
    first token from the previous reply's logits; QAIRT reuses part of the
    dialog. Every back-to-back retry or benchmark repeat is affected.
    """
    base = check_greedy(ctx)
    if base["answer"] != "yes":
        return {
            "answer": "inconclusive" if base["answer"] == "no" else base["answer"],
            "evidence": f"near-greedy replies differ even when spaced: {base['evidence']}",
        }
    replies, tokens, err = _twice(ctx, spaced=False, max_tokens=48, **_GREEDY)
    if err:
        return {"answer": "error", "evidence": err}
    same = replies[0] == replies[1]
    return {
        "answer": "yes" if same else "no",
        "evidence": f"back to back: prompt_tokens {tokens[0]} then {tokens[1]}; "
        + ("identical" if same else f"{replies[0][:50]!r} vs {replies[1][:50]!r}"),
    }


def check_seed(ctx):
    return _agree(
        ctx,
        "T=1.0, seed 1234, another request between",
        max_tokens=48,
        temperature=1.0,
        seed=1234,
    )


def check_thinks(ctx):
    _, body, err = _chat(ctx, _user("What is 2 + 3?"), max_tokens=512, temperature=0)
    if err:
        return {"answer": "error", "evidence": err}
    text = _content(body)
    msg = ((body.get("choices") or [{}])[0]).get("message") or {}
    separate = msg.get("reasoning_content") or msg.get("reasoning")
    counted = (_usage(body).get("completion_tokens_details") or {}).get(
        "reasoning_tokens"
    )
    thinks = "<think>" in text or "</think>" in text or bool(separate) or bool(counted)
    return {
        "answer": "yes" if thinks else "no",
        "evidence": f"{_usage(body).get('completion_tokens')} tokens: "
        f"{(text or separate or '')[:80]!r}"
        + (" (separate reasoning field)" if separate else ""),
    }


# A counting model reaches "7"; a thinking one writes "the user" first.
_STOPS = ["7", "user"]


def check_stop(ctx):
    """Does chat honour a stop sequence? Inconclusive if none ever came up."""
    _, body, err = _chat(
        ctx,
        _user("Count from 1 to 20, separated by spaces. Output only the numbers."),
        max_tokens=256,
        temperature=0,
        stop=_STOPS,
    )
    if err:
        return {"answer": "error", "evidence": err}
    text = _content(body)
    if any(s in text for s in _STOPS):
        answer = "no"
    elif _finish(body) == "stop":
        answer = "yes"
    else:  # the budget ran out before any stop string was due
        answer = "inconclusive"
    return {
        "answer": answer,
        "evidence": f"stop={_STOPS} -> finish_reason={_finish(body)!r}, content={text[-60:]!r}",
    }


def check_completions_stop(ctx):
    body = {
        "model": ctx["model"],
        "prompt": "1, 2, 3, 4,",
        "max_tokens": 24,
        "stop": ["7"],
    }
    try:
        with post_json(
            f"{ctx['base_url']}/v1/completions",
            body,
            entry=ctx["entry"],
            timeout=_timeout(ctx),
        ) as r:
            reply = r.json()
    except urllib.error.HTTPError as e:
        return {
            "answer": "error",
            "evidence": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}",
        }
    except Exception as e:
        return {"answer": "error", "evidence": f"{type(e).__name__}: {e}"[:300]}
    text = ((reply.get("choices") or [{}])[0]).get("text") or ""
    # "yes" needs the sequence to have reached 6 and stopped before 7: an empty
    # 200, or a reply that wandered off, never reached the stop string at all.
    if "7" in text:
        answer = "no"
    elif "6" in text:
        answer = "yes"
    else:
        answer = "inconclusive"
    return {
        "answer": answer,
        "evidence": f"/v1/completions stop=['7'] -> {text[:60]!r}",
    }


_READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Return the CONTENTS of one file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "file path"}},
            "required": ["path"],
        },
    },
}


def check_tool_calls(ctx):
    _, body, err = _chat(
        ctx,
        _user("Read the file README.md."),
        tools=[_READ_FILE],
        max_tokens=1024,
        temperature=0,
    )
    if err:
        return {"answer": "error", "evidence": err}
    msg = ((body.get("choices") or [{}])[0]).get("message") or {}
    calls = msg.get("tool_calls") or []
    name = calls[0].get("function", {}).get("name") if calls else None
    return {
        "answer": "yes" if name == "read_file" else "no",
        "evidence": f"finish_reason={_finish(body)!r}, tool_calls={[c.get('function', {}).get('name') for c in calls]}, "
        f"content={_content(body)[:60]!r}",
    }


# The prompt never asks for JSON, so a reply of exactly this object is the
# server constraining the output, not the model being obliging.
_COLOURS = ("red", "yellow", "blue")
_COLOUR_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "colour_letters",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "colour": {"type": "string", "enum": list(_COLOURS)},
                "letters": {"type": "integer"},
            },
            "required": ["colour", "letters"],
            "additionalProperties": False,
        },
    },
}
_FENCED = re.compile(r"^```[\w-]*\s*\n(.*?)\n?```$", re.DOTALL)


def _fits_colour_schema(text):
    try:
        value = json.loads(text)
    except ValueError:
        return None
    letters = value.get("letters") if isinstance(value, dict) else None
    return (
        isinstance(value, dict)
        and set(value) == {"colour", "letters"}
        and value["colour"] in _COLOURS
        and isinstance(letters, int)
        and not isinstance(letters, bool)
    )


def _schema_outcome(answer):
    """'honoured', 'fenced', 'json_only' or 'ignored' for one reply's answer.

    `fenced`: the schema's object inside a code fence -- the model was steered
    (a schema pasted into the prompt, say) but the output was not constrained,
    and a client's json.loads fails on it. `json_only`: JSON, not the schema's.
    """
    fits = _fits_colour_schema(answer)
    if fits is None:
        fence = _FENCED.match(answer)
        return "fenced" if fence and _fits_colour_schema(fence.group(1)) else "ignored"
    return "honoured" if fits else "json_only"


def check_response_format(ctx):
    """Is a response_format json_schema honoured, ignored or refused?

    Refused (a 4xx) breaks every request that carries the field; ignored costs
    only the shape. Both answer "no", and `outcome` says which -- `--diff`
    compares answers, so honoured <-> not is what it flags. Thinking is
    stripped first; a reply that never left it is inconclusive. The budget is
    a thinking lane's: v0.7.0's CPU lane spent 354 tokens thinking about
    "What is 2 + 3?" (emits_think), and a cut reply decides nothing.
    """
    _, body, err = _chat(
        ctx,
        _user("Name one primary colour and say how many letters its name has."),
        max_tokens=1024,
        temperature=0,
        response_format=_COLOUR_FORMAT,
    )
    if err:
        if err.startswith("HTTP 4"):
            return {
                "answer": "no",
                "outcome": "refused",
                "evidence": f"refused: {err[:200]}",
            }
        return {"answer": "error", "evidence": err}
    content = _content(body)
    answer = split_answer(content)[1].strip()
    if not answer:
        return {
            "answer": "inconclusive",
            "outcome": None,
            "evidence": f"no answer, finish_reason={_finish(body)!r}: {content[:80]!r}",
        }
    outcome = _schema_outcome(answer)
    return {
        "answer": "yes" if outcome == "honoured" else "no",
        "outcome": outcome,
        "evidence": f"{outcome}: {answer[:100]!r}",
    }


def _prefix_timings(ctx):
    """Cold, repeat, multi-turn extend and shared-prefix fork, measured once.

    `extend` is an agent turn: the whole cached conversation plus a reply and
    a new message. `fork` keeps only the long prefix and changes the tail —
    many questions over one preamble. A cache can serve the first and not the
    second (the v0.6.1 llama.cpp lane: 0.18 s repeat, full re-prefill fork).
    """
    if "_prefix" not in ctx:
        nonce = f"Session {random.SystemRandom().randrange(10**9)}."  # no stale cache
        prefix = f"{nonce} {filler(ctx['prefix_tokens'])}"
        tail = filler(300, seed=99) + "\n\nReply with the single word: ok"
        first = _user(prefix + "\n\nReply with the single word: ok")
        runs = (
            ("cold", first),
            ("repeat", first),
            ("extend", [*first, {"role": "assistant", "content": "ok"}, *_user(tail)]),
            ("fork", _user(f"{prefix} {tail}")),
        )
        timings = {}
        # A fork re-prefills the whole prefix on GenieX: every request may be cold.
        timeout = prompt_timeout(ctx["prefix_tokens"], ctx.get("timeout"))
        for name, messages in runs:
            seconds, body, err = _chat(
                ctx, messages, timeout=timeout, max_tokens=1, temperature=0
            )
            if err:
                timings = {"error": f"{name}: {err}"}
                break
            timings[name] = (round(seconds, 2), _usage(body).get("prompt_tokens"))
        ctx["_prefix"] = timings
    return ctx["_prefix"]


def _cache_answer(ctx, name, share):
    t = _prefix_timings(ctx)
    if "error" in t:
        return {"answer": "error", "evidence": t["error"]}
    cold, cold_tok = t["cold"]
    seconds, tokens = t[name]
    evidence = f"cold {cold}s ({cold_tok} tok) -> {name} {seconds}s ({tokens} tok)"
    if cold <= 0.5:  # below request overhead a cache cannot show; --prefix-tokens
        return {"answer": "inconclusive", "evidence": evidence + " (cold too fast)"}
    return {
        "answer": "yes" if seconds < share * cold else "no",
        "evidence": evidence,
    }


def check_prefix_cache(ctx):
    out = _cache_answer(ctx, "repeat", 0.3)
    t = ctx["_prefix"]
    if "error" not in t:
        cold, cold_tok = t["cold"]
        out["prefill_tok_per_s"] = (
            round(cold_tok / cold, 1) if cold_tok and cold else None
        )
        out["timings_s"] = {k: v[0] for k, v in t.items()}
        out["prompt_tokens"] = {k: v[1] for k, v in t.items()}
    return out


def check_prefix_extend(ctx):
    return _cache_answer(ctx, "extend", 0.5)


def check_prefix_fork(ctx):
    return _cache_answer(ctx, "fork", 0.5)


def check_overflow(ctx):
    n = ctx.get("overflow_tokens") or 0
    if not n:
        return {
            "answer": "skipped",
            "evidence": "pass --overflow-tokens N (more than the lane's context)",
        }
    seconds, body, err = _chat(
        ctx,
        _user(filler(n) + "\n\nReply with the single word: ok"),
        timeout=prompt_timeout(n, ctx.get("timeout")),
        max_tokens=16,
        temperature=0,
    )
    if err:
        # A 4xx naming the context is a refusal; a 5xx that mentions it
        # ("Context create from binary failed") is the server falling over.
        refused = err.startswith("HTTP 4")
        clean = refused and (
            "context_length_exceeded" in err or "context" in err.lower()
        )
        return {
            "answer": "yes" if clean else "no",
            "evidence": f"~{n} tokens -> {err[:160]} after {seconds:.1f}s",
        }
    return {
        "answer": "no",
        "evidence": f"~{n} tokens -> HTTP 200 after {seconds:.1f}s, "
        f"{_usage(body).get('completion_tokens')} tokens: {_content(body)[:40]!r}",
    }


def check_power_mode(ctx):
    """Is `power_mode` VALIDATED -- a known value accepted, nonsense refused?

    A server that ignores unknown fields answers 200 to a nonsense value too,
    so the invalid value is the discriminating one. "yes" says nothing about an
    effect: GenieX v0.7.0 applies it on the QAIRT lane (HTP vote perf_profile
    5 -> 8) and validates-then-ignores it on the CPU lane. On both it is part
    of the model's cache key, so each change reloads the model (12.7 s NPU,
    3.5 s CPU); the check ends with a plain request, which reloads it back, so
    the lane is left as it was launched and the next tool does not pay that.
    """
    answers = {}
    for value in ("power_saver", "turbo", None):
        extra = {"power_mode": value} if value else {}
        seconds, _, err = _chat(
            ctx,
            _user("Reply with the single word: ok"),
            max_tokens=4,
            temperature=0,
            **extra,
        )
        answers[value or "restore (no power_mode)"] = (
            f"accepted in {seconds:.1f}s" if err is None else err[:120]
        )
    understood = answers["power_saver"].startswith("accepted") and not answers[
        "turbo"
    ].startswith("accepted")
    return {"answer": "yes" if understood else "no", "evidence": answers}


# An explicit system message, and the same text twice: their difference is
# the text's own cost, which separates it from a system turn's framing.
_SYSTEM_PROBE = "Answer every question as briefly as you can, in plain English."
_SYSTEM_PLAN = (
    ("system", _SYSTEM_PROBE),
    ("none", None),
    ("system_twice", f"{_SYSTEM_PROBE} {_SYSTEM_PROBE}"),
    ("none_again", None),
)
_SELF_REPORT = (
    "Before this message, were you given a system message or any other "
    "instructions? If so, quote them word for word; if not, reply exactly: NONE"
)


def _system_prompt_tokens(ctx):
    """prompt_tokens of the four _SYSTEM_PLAN requests -> dict, or an error string.

    They alternate with and without a system turn, and each user message
    opens with one run nonce plus its own index (the same digits on all four,
    so they cost the same). A prefix cache, whose prompt_tokens count only
    what it prefilled, then shares just the template's first tokens between
    neighbours -- the same few on every request, once a throwaway request
    with no system turn has gone first: otherwise the first request's share
    depends on whatever the lane served before (an empty cache after a
    reload, or a system turn that shares its framing), and an `--only` run
    reads a token or two of that as a hidden prompt.
    """
    nonce = random.SystemRandom().randrange(10**9)
    _chat(ctx, _user(f"Request {nonce}. Reply with the single word: ok"), max_tokens=1)
    tokens = {}
    for i, (name, system) in enumerate(_SYSTEM_PLAN):
        messages = [{"role": "system", "content": system}] if system else []
        messages += _user(f"Request {nonce}-{i}. Reply with the single word: ok")
        _, body, err = _chat(ctx, messages, max_tokens=4, temperature=0)
        if err:
            return f"{name}: {err}"
        tokens[name] = _usage(body).get("prompt_tokens")
    return tokens


def _system_verdict(tokens):
    """(answer, note, hidden_tokens) from the four prompt_tokens counts."""
    counts = [tokens.get(name) for name, _ in _SYSTEM_PLAN]
    if not all(isinstance(c, int) and c > 0 for c in counts):
        return "inconclusive", "prompt_tokens missing or zero", None
    if abs(tokens["none"] - tokens["none_again"]) > 2:
        return "inconclusive", "the two no-system requests disagree", None
    text = tokens["system_twice"] - tokens["system"]
    added = tokens["system"] - (tokens["none"] + tokens["none_again"]) / 2
    hidden = round(text - added, 1)
    note = f"its text costs {text}, the message added {added:g} -> {hidden:g} hidden"
    if text <= 0:
        return "inconclusive", note + " (a longer system message cost nothing)", None
    if hidden >= 2:
        return "yes", note + ": an explicit system message replaces that many", hidden
    if hidden <= -2:
        return "no", note + ": a system turn's framing, no default displaced", hidden
    return "inconclusive", note, hidden


def check_bundle_system_prompt(ctx):
    """With no system message, is a default system prompt sent anyway?

    GenieX v0.7 reads a QAIRT bundle's system prompt from its metadata ("You
    are a helpful AI assistant." for the 4B Instruct bundle), and a chat
    template may add its own; either way the model sees instructions no
    request carried. Read from usage, not from the model: an explicit system
    message REPLACES a default, so it costs its text plus a turn's framing
    when there is none, and its text MINUS the default when there is one.
    `hidden_tokens` is the text's cost minus what the message added: negative
    (the framing) without a default, the default's size with one. A default
    sent IN ADDITION to an explicit message is invisible and reads as "no", so
    "no" means no default that a system message displaces. Inconclusive where
    a longer message does not grow prompt_tokens. `self_report` asks the model to
    quote its instructions and never votes: a model invents a system prompt as
    readily as it quotes one.
    """
    tokens = _system_prompt_tokens(ctx)
    if isinstance(tokens, str):
        return {"answer": "error", "evidence": tokens}
    answer, note, hidden = _system_verdict(tokens)
    _, body, err = _chat(ctx, _user(_SELF_REPORT), max_tokens=512, temperature=0)
    if err:
        self_report = f"error: {err[:120]}"
    else:
        self_report = split_answer(_content(body))[1].strip()[:200] or (
            f"(no answer, finish_reason={_finish(body)!r})"
        )
    return {
        "answer": answer,
        "evidence": f"prompt_tokens {tokens}: {note}",
        "prompt_tokens": tokens,
        "hidden_tokens": hidden,
        "self_report": self_report,
    }


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
        with open(args.diff[0]) as f:
            old = json.load(f)
        with open(args.diff[1]) as f:
            new = json.load(f)
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
    tool_files = ("contract.py",)
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
