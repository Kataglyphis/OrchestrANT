"""One speed summary per run: the headline figures every printer shows.

On 2026-09-24 one report, the GenieX upgrade run's `v070-npu-speed.json`, read
three ways. The speed runner's table printed `Tokens/sec 18.3 avg`, `Overall
... 25.4 tok/s` and `Decode only 19.7 tok/s avg`; `report table` printed
`T/s: 18.3`; the viewer charted that 18.3 as "Tokens per Second (overall)",
the name the runner gave 25.4. Each printer averaged its own way: "Overall"
divided prompt AND completion tokens by the wall time (1763 tokens, 427 of
them the prompt), and every other figure was a mean of per-request rates, so
an 8-token reply weighed as much as a 256-token one.

Every figure is now computed here, once. `print_table`, `report summary`,
`report table` and the viewer -- through the `speed` block `report manifest`
writes -- all read the dict `summarise` returns. Rates are ratios of sums, the
tokens over the seconds they took pooled across requests, as the CPU-rail
joules per token already were (hostload.summary_lines) and for the same
reason: a short request's timing noise must not outweigh a long request's
measurement. Times are means.

Which rows count: every request that completed, cut at `max_tokens` or not,
thinking or not -- its tokens were produced and timed either way. Only an
errored request leaves the figures, and it is counted apart. Time to an
ANSWER is another matter, and answers.py owns it: a cut reply has none.
"""

from __future__ import annotations


def completed(rows):
    """The requests that returned a reply: every row without an `error` key.

    The key, not its value: the runner writes `str(e)`, which is "" for an
    exception raised without a message, and such a row still has no tokens or
    timings to count.
    """
    return [r for r in rows if "error" not in r]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _seconds(row, n, rate_key, seconds_key):
    """The seconds behind one row's rate: recorded, or read back from the rate."""
    if seconds_key and _number(row.get(seconds_key)):
        return row[seconds_key]
    rate = row.get(rate_key)
    return n / rate if _number(rate) and rate > 0 else None


def _pooled_rate(rows, amount, rate_key, seconds_key=None):
    """Sum of amounts over the sum of the seconds they took, or None.

    Each row's seconds are its `seconds_key` field where it has one, else read
    back from its own rate (amount / rate): the runner computed the rate from
    unrounded times, while `latency_s` and `ttft_s` are stored rounded to 10 ms
    and 1 ms -- nearly 2 % of the 0.3 s decode window of an 8-token reply.
    """
    pairs = [(amount(r), r) for r in rows]
    pairs = [(n, _seconds(r, n, rate_key, seconds_key)) for n, r in pairs if n > 0]
    pairs = [(n, s) for n, s in pairs if s is not None and s > 0]
    if not pairs:
        return None
    return sum(n for n, _ in pairs) / sum(s for _, s in pairs)


def decode_tok_s(rows):
    """Tokens decoded after the first, over the seconds spent decoding them.

    Answers "how fast does this lane generate once it has started?" for the
    run as a whole: the sum of `completion_tokens - 1` over the sum of the
    decode windows (`decode_s`, or read back from `decode_tok_per_sec` in a
    report older than it) of the streamed rows of more than one token. A row
    whose window was too short for a rate of its own still counts, as it did
    when it carried one (answers.decode_fields). The first token belongs to
    the prefill, as in the row fields.

    Pooled, not a mean of per-request rates: the GenieX v0.6.1 -> v0.7.0 NPU
    loss read -13.4 % as that mean, dragged by the 8- and 12-token replies
    whose rates moved -6.5 % and -8.6 %; pooled it reads -14.8 %, and the
    per-prompt median `bench_compare` prints is -14.7 %. On the 2048-token
    `v070r2-cpu-speed-answer` the mean said 19.2 tok/s for a run that decoded
    9928 tokens at 13.9: five replies of 144-815 tokens at 20-27 tok/s
    counted as much as the four of 1666-2048 at 11.8-13.4.
    """
    return _pooled_rate(
        rows,
        lambda r: (r.get("completion_tokens") or 0) - 1,
        "decode_tok_per_sec",
        "decode_s",
    )


def prefill_tok_s(rows):
    """Prompt tokens over the time to the first token, pooled across requests.

    The sum of `prompt_tokens` over the sum of TTFTs, over the rows with a
    `prefill_tok_per_sec`. A TTFT carries the request's fixed overhead too, so
    on the speed runner's 11-158-token prompts this reads far below what the
    lane prefills at agent sizes (about 300 tok/s on the NPU lane, where the
    contract's cold 1.8k-token prefill measured about 1000): compare it between
    runs of one prompt set, not with a prefill benchmark.
    """
    return _pooled_rate(
        rows, lambda r: r.get("prompt_tokens") or 0, "prefill_tok_per_sec"
    )


def overall_tok_s(rows):
    """Completion tokens over the whole wall time: what a caller received.

    Answers "sending these prompts one after another, how many tokens per
    second came back?": the sum of `completion_tokens` over the sum of
    `latency_s`, prefill, thinking and decode all inside the time. Prompt
    tokens are not output. The runner's old "Overall" line counted them and
    read 25.4 tok/s for a lane that generated 19.3, and a CPU lane's 31-32
    from that line was once published as its decode rate (the page's
    correction: ~30). A request that returned no tokens still cost its wall
    time, so it stays in the denominator.
    """
    timed = [
        r
        for r in rows
        if _number(r.get("completion_tokens")) and _number(r.get("latency_s"))
    ]
    seconds = sum(r["latency_s"] for r in timed)
    if seconds <= 0:
        return None
    return sum(r["completion_tokens"] for r in timed) / seconds


def ttft_s(rows):
    """Mean seconds to the first token of any kind, thinking included.

    A time, not a rate, so a plain mean: what one request waits on average
    before anything arrives. Over the rows that measured one; None, not 0,
    when none did -- a non-streamed run has no first-token moment, and 0.00 s
    would claim an instant one.
    """
    times = [r["ttft_s"] for r in rows if _number(r.get("ttft_s"))]
    return sum(times) / len(times) if times else None


def _spread(rows, key):
    """[lowest, highest] of one row field, or None: the per-request range."""
    values = [r[key] for r in rows if _number(r.get(key))]
    return [min(values), max(values)] if values else None


def summarise(rows):
    """The run's headline figures, for every printer to read.

    `rows` is the report's `results` as the runner wrote them, errors
    included. `per_request` keeps the range of each row field behind a
    headline, so a printer can show the spread without averaging it again.
    """
    ok = completed(rows)
    return {
        "requests": len(ok),
        "errored": len(rows) - len(ok),
        # Counted from stream chunks where the server sent no usage.
        "estimated": sum(1 for r in ok if r.get("tokens_estimated")),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in ok),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in ok),
        "wall_s": sum(r["latency_s"] for r in ok if _number(r.get("latency_s"))),
        "overall_tok_s": overall_tok_s(ok),
        "decode_tok_s": decode_tok_s(ok),
        "prefill_tok_s": prefill_tok_s(ok),
        "ttft_s": ttft_s(ok),
        "per_request": {
            key: _spread(ok, key)
            for key in ("tokens_per_sec", "decode_tok_per_sec", "ttft_s")
        },
    }


def _range(spread, spec):
    return f"  (per request {spread[0]:{spec}}-{spread[1]:{spec}})" if spread else ""


def summary_lines(s):
    """The speed runner's token, rate and TTFT lines, from one summary."""
    lines = []
    per = s["per_request"]
    if s["overall_tok_s"] is not None:
        ct, n = s["completion_tokens"], s["requests"]
        lines.append(f"    Completion tok: {ct} total  /  {ct / n:.1f} avg per req")
        lines.append(
            f"    Overall:        {s['overall_tok_s']:.1f} tok/s  = {ct} tokens / "
            f"{s['wall_s']:.1f}s, prefill and thinking included"
            + _range(per["tokens_per_sec"], ".1f")
        )
    if s["ttft_s"] is None:
        lines.append("    TTFT:           not measured — re-run with --stream")
    else:
        low, high = per["ttft_s"]
        lines.append(
            f"    TTFT:           {low:.2f}s  /  {s['ttft_s']:.2f}s avg  /  {high:.2f}s max"
        )
    if s["decode_tok_s"] is not None:
        lines.append(
            f"    Decode:         {s['decode_tok_s']:.1f} tok/s  after each first token"
            + _range(per["decode_tok_per_sec"], ".1f")
        )
    if s["prefill_tok_s"] is not None:
        lines.append(
            f"    Prefill:        {s['prefill_tok_s']:.0f} tok/s  prompt tokens / TTFT, "
            "request overhead included"
        )
    if s["estimated"]:
        lines.append(
            f"    Estimated:      {s['estimated']} of {s['requests']} token counts "
            "are stream chunks: the server sent no usage"
        )
    return lines
