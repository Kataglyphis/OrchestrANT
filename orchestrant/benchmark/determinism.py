"""Does this lane sample at temperature 0? The evidence bench_compare trusts.

A report's `determinism_probe` decides how bench_compare reads it: a lane
recorded deterministic has its single-draw flips judged strictly, as real
regressions. So a change to this probe changes what a report CLAIMS, and the
tools that run it (bench_tools, bench_coding) put this file in their
`tool_sha256`.

It used to live in provenance.py, and those tools fingerprinted that whole
module for its sake — so every edit to the provenance plumbing (a new field, a
runtime-detection fix) read on the next comparison as "BENCHMARK SOURCE
CHANGED — a score difference may be the grader", while the grader was
provably untouched. provenance re-exports these names, so existing imports
keep working.
"""

import hashlib


PROBE_PROMPT = "Write one sentence about the sea."


SPACER_PROMPT = "Reply with the single word: ok"


def determinism_probe(base_url, model, post, prompt=PROBE_PROMPT, max_tokens=48):
    """Send the same request twice, with another between; did the outputs match?

    `post(url, payload) -> dict` is injected so this can run without a server
    (tests) and so callers pick the transport. "Deterministic" here means two
    draws at temperature 0 agreed byte-for-byte — evidence, not proof, and it is
    recorded as such so a --repeats 1 flip can be read for what it is.

    The prompt must leave the model real choices. The first version asked for
    "the single word: ready" in 8 tokens — an answer with almost no entropy,
    which a SAMPLING lane repeats verbatim. On GenieX v0.6.1 it recorded the
    QAIRT lane as deterministic while two open-ended requests at temperature 0
    came back different, and bench_compare then read that lane's single-draw
    flips as real regressions.

    The two draws are NOT sent back to back. On GenieX (v0.6.1 and v0.7.0,
    measured 2026-09-24) an identical request sent twice in a row takes a cache
    path that changes the reply on both lanes — llama.cpp prefills 0 tokens and
    samples the first token from the previous reply's logits; QAIRT reuses
    part of the dialog — while after any other request both answer as if cold.
    A spacer request between the draws measures the sampler, not that bug.
    """
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    spacer = {
        **payload,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": SPACER_PROMPT}],
    }
    outputs = []
    try:
        for i in range(2):
            if i:
                post(f"{base_url}/v1/chat/completions", spacer)
            reply = post(f"{base_url}/v1/chat/completions", payload)
            content = (
                (reply.get("choices") or [{}])[0].get("message", {}).get("content")
            )
            if content is None:
                raise ValueError("reply carried no message content")
            outputs.append(content)
    except Exception as e:
        return {
            "deterministic": None,
            "requests": len(outputs),
            "prompt": prompt,
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    return {
        "deterministic": outputs[0] == outputs[1],
        "requests": 3,
        "spacer": True,
        "prompt": prompt,
        "output_sha256": [hashlib.sha256(o.encode()).hexdigest()[:16] for o in outputs],
        "error": None,
    }
