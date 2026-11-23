"""The hub's shared OpenAI stream lines and whole answers, replayed through answers.py (ANTfrastructure CON237, CON288)."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrant.benchmark.answers import (
    Reply,
    from_text,
    read_stream,
    stream_chunks,
    usage_counts,
)


CONTRACTS = (
    Path(__file__).resolve().parents[3] / "third_party/ANTfrastructure/shared/contracts"
)
DOC = json.loads((CONTRACTS / "openai-stream-lines.json").read_text(encoding="utf-8"))
ANSWERS = json.loads(
    (CONTRACTS / "openai-chat-answers.json").read_text(encoding="utf-8")
)


def _done(lines: list[str]) -> bool:
    """Whether [DONE] arrived: stream_chunks ends there, so a line after it is left unread."""
    tail = iter([*lines, 'data: {"sentinel": true}'])
    return not any(chunk.get("sentinel") for chunk in stream_chunks(tail))


def _outcome(read: Callable[..., Reply], given: object, expect: dict) -> dict:
    """What `read` makes of `given` in the file's terms; an expected error is matched as a substring."""
    if "error" in expect:
        # A chunk or body that is not an object is a TypeError, the server's other faults RuntimeError.
        with pytest.raises((RuntimeError, TypeError)) as caught:
            read(given)
        assert expect["error"] in str(caught.value), caught.value
        return {"error": expect["error"]}
    reply = read(given)
    return {
        "text": reply.content,
        "thinking": reply.reasoning,
        "finish": reply.finish_reason,
        "usage": usage_counts(reply.usage),
    }


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_every_shared_stream_case_reads_as_the_hub_file_says(case: dict) -> None:
    got = _outcome(read_stream, case["lines"], case["expect"])
    if "error" not in got:
        got["done"] = _done(case["lines"])
    assert got == case["expect"]


@pytest.mark.parametrize(
    "case", ANSWERS["cases"], ids=[c["name"] for c in ANSWERS["cases"]]
)
def test_every_shared_whole_answer_reads_as_the_hub_file_says(case: dict) -> None:
    assert _outcome(from_text, case["body"], case["expect"]) == case["expect"]


def test_the_files_still_hold_their_cases() -> None:
    assert len(DOC["cases"]) >= 22
    assert len(ANSWERS["cases"]) >= 10
