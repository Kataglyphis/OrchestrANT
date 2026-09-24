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
"""

from __future__ import annotations

import json
import time


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
    thinking, answer = split_answer(content, reasoning)
    total = len(content) + len(reasoning)
    cut = reply.finish_reason == "length" or (
        reply.finish_reason is None
        and bool(max_tokens)
        and bool(completion_tokens)
        and completion_tokens >= max_tokens
    )
    return {
        "finish_reason": reply.finish_reason,
        "answered": bool(answer.strip()) and not cut,
        "thinking_char_share": round(thinking / total, 3) if total else None,
    }


def row_thinking_share(row):
    """A row's thinking share, repairing the one an older report got wrong.

    Before `answered` existed, a `<think>` that never closed scored 0.0; such a
    row's preview still opens with the tag, so it is read back as all thinking.
    """
    share = row.get("thinking_char_share")
    if (
        share == 0.0
        and "answered" not in row
        and str(row.get("content_preview") or "").lstrip().startswith("<think>")
    ):
        return 1.0
    return share


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
    shares = [s for s in map(row_thinking_share, rows) if s is not None]
    if shares and any(shares):
        lines.append(
            f"    Thinking share: {100 * _mean(shares):.0f}% of output was thinking "
            "(pure latency for an agent)"
        )
    return lines
