"""Does the server still behave the way the tooling assumes?

Each check records a neutral answer (yes, no, inconclusive, error, skipped) and
its evidence, never a pass or fail; `--diff` names what moved between runtimes.

The check table and the command line are in contract_cli.py.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
import urllib.error

from orchestrant.benchmark.answers import split_answer, stream_chunks
from orchestrant.benchmark.client import post_json


# Fixed vocabulary and seed: a changed prefill time is the server, not the prompt.
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
# The slowest cold prefill measured, halved because the rate falls with depth.
SLOWEST_PREFILL_TOK_S = 6


def prompt_timeout(tokens, timeout=None):
    """Seconds a `tokens`-long prompt may take: --timeout or, if longer, its slowest prefill."""
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
    """Does the server stop EARLIER than max_tokens asks -- and where?"""
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
    """Is usage.prompt_tokens still the prompt's size when the prompt is cached?"""
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
            # The shared reader: an error sent inside the stream is the answer "error", not one more chunk.
            for chunk in stream_chunks(r.lines()):
                chunks += 1
                usage = chunk.get("usage") or usage
    except Exception as e:
        return {"answer": "error", "evidence": f"{type(e).__name__}: {e}"[:300]}
    # A unique prompt, so no cache hit zeroes prompt_tokens and nulls the prefill rate.
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
# Near-greedy anywhere; NOT temperature 0 (see check_temperature0_is_greedy).
_GREEDY = {"temperature": 0.01, "top_k": 1}


def _spacer(ctx):
    """An unrelated request, so the next one is not an identical follow-up."""
    _chat(ctx, _user("Reply with the single word: ok"), max_tokens=1)


def _twice(ctx, spaced=True, **params):
    """The same request twice, spaced by default -> ([text, text], [prompt_tokens, ...], error)."""
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
    """Is temperature 0 greedy decoding, or read as unset?"""
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
    """Does an identical request sent twice IN A ROW get the same reply?"""
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
    # "yes" needs the sequence to reach 6 and stop before 7.
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


# The prompt never asks for JSON, so exactly this object means the server constrained it.
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
    """'honoured', 'fenced', 'json_only' or 'ignored' for one reply's answer."""
    fits = _fits_colour_schema(answer)
    if fits is None:
        fence = _FENCED.match(answer)
        return "fenced" if fence and _fits_colour_schema(fence.group(1)) else "ignored"
    return "honoured" if fits else "json_only"


def check_response_format(ctx):
    """Is a response_format json_schema honoured, or (both "no") ignored or refused?"""
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
    """Cold, repeat, multi-turn extend and shared-prefix fork, measured once."""
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
        # A 4xx naming the context is a refusal; a 5xx mentioning it is the server falling over.
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
    """Is `power_mode` VALIDATED -- a known value accepted, nonsense refused?"""
    answers = {}
    # Ends plain: power_mode is in the model's cache key, so this reloads the launch state.
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


# The same text twice: their difference is the text's cost, apart from a system turn's framing.
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
    """prompt_tokens of the four _SYSTEM_PLAN requests -> dict, or an error string."""
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

    Read from usage: "no" means no default that a system message displaces;
    `self_report` never votes, as a model invents a system prompt as readily as it quotes one.
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
