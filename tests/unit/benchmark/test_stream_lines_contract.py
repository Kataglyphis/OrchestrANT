"""The hub's shared OpenAI stream lines, replayed through read_stream (ANTfrastructure CON237)."""

import json
from pathlib import Path

import pytest

from orchestrant.benchmark.answers import read_stream, stream_chunks


CASES = (
    Path(__file__).resolve().parents[3]
    / "third_party/ANTfrastructure/shared/contracts/openai-stream-lines.json"
)
DOC = json.loads(CASES.read_text(encoding="utf-8"))


def _done(lines: list[str]) -> bool:
    """Whether [DONE] arrived: stream_chunks ends there, so a line after it is left unread."""
    tail = iter([*lines, 'data: {"sentinel": true}'])
    return not any(chunk.get("sentinel") for chunk in stream_chunks(tail))


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_every_shared_stream_case_reads_as_the_hub_file_says(case: dict) -> None:
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(RuntimeError, match=None) as caught:
            read_stream(case["lines"])
        assert expect["error"] in str(caught.value), caught.value
        return
    reply = read_stream(case["lines"])
    usage = reply.usage and [
        reply.usage["prompt_tokens"],
        reply.usage["completion_tokens"],
    ]
    got = {
        "text": reply.content,
        "thinking": reply.reasoning,
        "finish": reply.finish_reason,
        "usage": usage or None,
        "done": _done(case["lines"]),
    }
    assert got == expect


def test_the_file_still_holds_its_cases() -> None:
    assert len(DOC["cases"]) >= 11
