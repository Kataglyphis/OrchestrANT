"""What a reply is: which part was thinking, and whether an answer arrived.

The speed runner used to score a `<think>` block that never closed as 0 %
thinking, and to publish the time to the token cap as the "time to a finished
answer". Both were wrong in the same runs: on 2026-09-24, six of nine
CPU-lane replies (a thinking Qwen3-4B, `max_tokens` 256) never left
`<think>`, the printed "86 % thinking" averaged the three that did, and the
"12.5 s to a finished answer" was 12.5 s to the cap. A reply that ran out of
budget has no answer, and its time to one was not measured.

Servers put thinking in three places, and all three are read here: inline
`<think>` inside `content` (GenieX, llama.cpp without --reasoning-format),
`reasoning` (Ollama) and `reasoning_content` (llama-server, vLLM). Reading
only `content` made a reasoning_content server's whole thinking phase count
as time to first token.

A reply's decode rate is read here too (decode_fields), and only where the
stream gave it a window long enough to time.

A thinking share is not always there to read: a reply cut before any marker
may be all thinking (thinking_share), and its share is recorded as unknown.
"""

from __future__ import annotations

import json
import time


# The shortest decode window a row's rate is read from; decode_fields says why.
MIN_DECODE_WINDOW_S = 0.05

# Why a reply with text has no thinking share; thinking_share says when.
UNKNOWN_SHARE_NOTE = (
    "cut before any <think>, </think> or reasoning: a chat template that opens "
    "<think> in the prompt leaves no marker, so this reply may be all thinking"
)


def delta_pieces(delta):
    """(answer, thinking) text of one streamed delta or one whole message."""
    answer = delta.get("content") or ""
    thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
    return answer, thought


def split_answer(content, reasoning=""):
    """(thinking characters, answer text) of a whole reply.

    Inline `<think>` that never closed is ALL thinking: the answer never began.
    """
    if "</think>" in content:
        answer = content.rsplit("</think>", 1)[1]
    elif "<think>" in content:
        answer = ""
    else:
        answer = content
    return len(reasoning) + len(content) - len(answer), answer


def thinking_share(content, reasoning="", cut=False):
    """(share, note): the thinking share of a whole reply, or None and why.

    Qwen3 and the Qwen3.8 distills open `<think>` in the PROMPT, so a reply
    carries only the closing tag, and one cut before it carries neither: the
    9B distill's two CUT rows of cpu-9b-classic-r3.json read 0.0 beside
    0.42-0.96 on the seven that finished. Such a reply is all thinking or all
    answer and cannot say which. A FINISHED reply with no marker keeps 0.0:
    a template that opened `<think>` closes it before the answer.
    """
    total = len(content) + len(reasoning)
    if not total:
        return None, None
    thinking, _ = split_answer(content, reasoning)
    if cut and not thinking:
        return None, UNKNOWN_SHARE_NOTE
    return round(thinking / total, 3), None


class Reply:
    """One reply, streamed or not, with the moments that matter."""

    def __init__(self):
        self._answer, self._thought = [], []
        self.usage = None
        self.finish_reason = None
        self.first_token_at = None  # any token, thinking included: end of prefill
        self.first_answer_at = None  # first visible answer token
        self.chunks = 0

    @property
    def content(self):
        return "".join(self._answer)

    @property
    def reasoning(self):
        return "".join(self._thought)

    def add(self, answer, thought, now=None):
        if not (answer or thought):
            return
        self.chunks += 1
        self._answer.append(answer)
        self._thought.append(thought)
        if now is None:
            return
        if self.first_token_at is None:
            self.first_token_at = now
        if (
            self.first_answer_at is None
            and answer
            and split_answer(self.content)[1].strip()
        ):
            self.first_answer_at = now


def read_stream(lines, clock=time.monotonic):
    """A Reply from SSE lines. The space after "data:" is optional (GenieX omits it)."""
    reply = Reply()
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("usage"):  # the final chunk; its choices may be empty
            reply.usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        reply.finish_reason = choices[0].get("finish_reason") or reply.finish_reason
        answer, thought = delta_pieces(choices[0].get("delta") or {})
        reply.add(answer, thought, clock())
    return reply


def from_body(body):
    """A Reply from a non-streamed response body."""
    reply = Reply()
    choice = (body.get("choices") or [{}])[0]
    reply.add(*delta_pieces(choice.get("message") or {}))
    reply.finish_reason = choice.get("finish_reason")
    reply.usage = body.get("usage")
    return reply


def accounting(reply, completion_tokens=None, max_tokens=None):
    """finish_reason, `answered` and the thinking share of one reply.

    A reply is answered when visible answer text arrived and the server did not
    stop it at the budget. Without a finish_reason, reaching max_tokens is read
    as a cut: a server that does not say why it stopped cannot claim a finish.
    """
    content, reasoning = reply.content, reply.reasoning
    _, answer = split_answer(content, reasoning)
    cut = reply.finish_reason == "length" or (
        reply.finish_reason is None
        and bool(max_tokens)
        and bool(completion_tokens)
        and completion_tokens >= max_tokens
    )
    share, note = thinking_share(content, reasoning, cut)
    return {
        "finish_reason": reply.finish_reason,
        "answered": bool(answer.strip()) and not cut,
        "thinking_char_share": share,
        "thinking_share_note": note,
    }


def decode_fields(elapsed, ttft, completion_tokens):
    """A row's decode window and rate, or its window and why it has no rate.

    The first token ends the prefill, so the window from it to the end of the
    reply decoded `completion_tokens - 1`. Ollama sent three 8-12-token
    replies of ollama-t8-4b-instruct-speed-answer.json in one burst, latency
    == TTFT: windows of 0.36-0.72 ms read 9,733-26,712 tok/s. The shortest
    real window in the 25 tracked speed reports is 174 ms (a 7-token reply at
    34.5 tok/s, cpu-llama3b). MIN_DECODE_WINDOW_S lies between them, and above
    three ticks of the coarsest clock this runner meets: time.monotonic on
    Windows before Python 3.13, 15.6 ms. A one-token reply decoded nothing.

    `decode_s` is kept either way, so speed_summary.decode_tok_s pools every
    row it pooled when the burst rows still carried their rates.
    """
    if ttft is None:  # not streamed: no first-token moment to start a window
        return {"decode_s": None, "decode_tok_per_sec": None, "decode_rate_note": None}
    window = elapsed - ttft
    note = None
    if completion_tokens < 2:
        note = "under 2 tokens: none decoded after the first"
    elif window < MIN_DECODE_WINDOW_S:
        note = (
            f"decode window {window * 1000:.1f} ms, "
            f"under the {MIN_DECODE_WINDOW_S * 1000:.0f} ms floor"
        )
    return {
        "decode_s": round(window, 6),
        "decode_tok_per_sec": (
            None if note else round((completion_tokens - 1) / window, 2)
        ),
        "decode_rate_note": note,
    }


def row_decode_rate(row):
    """A row's decode rate, withholding one an older report stored from a burst.

    A report written before decode_fields kept such a rate: the tracked t8
    run's rows 0-2 still read 9,733-26,712 tok/s, and paired against them a
    rerun that lost 20 % on the other six prompts read "noise +/-40%" and
    passed (compare_speed). Their window is read back as (completion_tokens -
    1) / rate; a row decode_fields wrote, with its `decode_s`, already says.
    """
    rate, tokens = row.get("decode_tok_per_sec"), row.get("completion_tokens")
    if not rate or "decode_s" in row or not isinstance(tokens, int):
        return rate or None
    return None if (tokens - 1) / rate < MIN_DECODE_WINDOW_S else rate


def row_thinking_share(row):
    """A row's thinking share, repairing the ones an older report got wrong.

    Before `answered` existed, a `<think>` that never closed scored 0.0; such a
    row's preview still opens with the tag, so it is read back as all thinking.
    Before `thinking_share_note` existed, a cut reply with no marker scored 0.0
    too; at three decimals 0.0 means no marker and no reasoning, so a CUT row
    of such a report is read back as unknown (thinking_share).
    """
    share = row.get("thinking_char_share")
    if share != 0.0 or "thinking_share_note" in row:
        return share
    if "answered" not in row and str(
        row.get("content_preview") or ""
    ).lstrip().startswith("<think>"):
        return 1.0
    return None if _row_cut(row) else share


def _row_cut(row):
    """Did a speed, chat or coding row stop at a budget or a deadline?"""
    unanswered = row.get("answered") is False and row.get("finish_reason") != "stop"
    return bool(row.get("truncated") or row.get("gave_up") or unanswered)


def row_thinking_unknown(row):
    """A row whose reply had text but whose thinking share cannot be read."""
    return row_thinking_share(row) is None and bool(
        row.get("thinking_share_note") or row.get("thinking_char_share") == 0.0
    )


def row_answer_s(row):
    """Seconds to a finished answer, or None when the row never finished one."""
    if "answered" in row:
        return row.get("wall_s_to_answer") if row["answered"] else None
    return row.get("wall_s_to_answer", row.get("latency_s"))


def answered_count(rows):
    """(answered, rows that say) -- (None, 0) for reports older than the field.

    A mean time to an answer is taken over the rows that HAVE one, so it must
    travel with this count: a run that cut 5 of 9 replies averages its 4
    shortest and would otherwise rank as the fastest.
    """
    flagged = [r for r in rows if "answered" in r]
    if not flagged:
        return None, 0
    return sum(1 for r in flagged if r["answered"]), len(flagged)


def answer_cell(row):
    """The table's answer column: seconds, or "cut" where none arrived."""
    seconds = row_answer_s(row)
    return "cut" if seconds is None else seconds


def _mean(values):
    return sum(values) / len(values)


def summary_lines(results):
    """The time-to-answer and thinking lines of the speed table's summary."""
    rows = [r for r in results if "error" not in r]
    lines = []
    if rows and all("answered" in r for r in rows):
        done = [r for r in rows if r["answered"]]
        cut = len(rows) - len(done)
        line = f"    Answered:       {len(done)}/{len(rows)} inside max_tokens"
        if cut:
            line += f"  ({cut} cut: their time to an answer was NOT measured)"
        lines.append(line)
        if done:
            line = (
                f"    Time to answer: {_mean([r['wall_s_to_answer'] for r in done]):.1f}s "
                f"avg over the {len(done)} answered"
            )
            lines.append(
                line + "  <-- rank models by THIS, not tok/s"
                if not cut
                else line + "  -- raise --max-tokens until all answer, then rank"
            )
        ttfa = [r["ttfa_s"] for r in done if r.get("ttfa_s") is not None]
        if ttfa:
            lines.append(
                f"    First answer:   {_mean(ttfa):.2f}s avg  (first token after any thinking)"
            )
    else:  # a report written before `answered` existed
        answers = [s for s in map(row_answer_s, rows) if s is not None]
        if answers:
            lines.append(
                f"    Time to answer: {_mean(answers):.1f}s avg  (older report: "
                "rows cut at max_tokens are included and not identified)"
            )
    thinking = _thinking_line(rows)
    return [*lines, thinking] if thinking else lines


def _thinking_line(rows):
    """The mean thinking share, and how many cut replies it cannot count.

    Averaging only the rows that show a share reads a run whose cut replies
    were all thinking as the share of the ones that finished.
    """
    shares = [s for s in map(row_thinking_share, rows) if s is not None]
    unknown = sum(map(row_thinking_unknown, rows))
    cut = f"{unknown} cut before any <think> marker"
    if not shares:
        return f"    Thinking share: unknown: {cut}" if unknown else None
    if not (unknown or any(shares)):
        return None
    line = f"    Thinking share: {100 * _mean(shares):.0f}% of output was thinking"
    if not unknown:
        return line + " (pure latency for an agent)"
    them = "reply that shows" if len(shares) == 1 else "replies that show"
    return f"{line} on the {len(shares)} {them} it; {cut}: unknown"
