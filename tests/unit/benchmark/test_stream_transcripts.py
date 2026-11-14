"""Recorded SSE transcripts through every stream reader of the lab: an error inside a stream is an error in each."""

import re
import urllib.request
from pathlib import Path

import pytest

from orchestrant.benchmark import contract, lanes
from orchestrant.benchmark.answers import read_stream


STREAMS = Path(__file__).with_name("streams")

# Per transcript: the answer, the thinking, the counted pieces, whether a usage chunk came, or the error it carries.
EXPECTED = {
    "usage.sse": {"content": "The sea.", "reasoning": "", "pieces": 2, "usage": True},
    "no-space.sse": {"content": "ok", "reasoning": "", "pieces": 2, "usage": True},
    "error-object.sse": {"error": "the model process ended"},
    "error-line.sse": {"error": "upstream connection closed"},
    "cut.sse": {"content": "The se", "reasoning": "", "pieces": 2, "usage": False},
    "reasoning.sse": {
        "content": "Paris.",
        "reasoning": "Capital of France. Easy.",
        "pieces": 3,
        "usage": True,
    },
}
CTX = {"base_url": "http://lane:1", "model": "m", "entry": {}}


def lines(name):
    return (STREAMS / name).read_text(encoding="utf-8").splitlines()


class _Response:
    """urlopen's response: the transcript's raw lines, as an HTTP body iterates."""

    def __init__(self, name):
        self.raw = [f"{line}\n".encode() for line in lines(name)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.raw)


class _Stream:
    """post_json's streamed reply: the transcript's text lines."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def lines(self):
        yield from lines(self.name)


def test_every_transcript_has_its_expectation():
    assert sorted(path.name for path in STREAMS.glob("*.sse")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_answer_reader(name):
    want = EXPECTED[name]
    if "error" in want:
        with pytest.raises(RuntimeError, match=re.escape(str(want["error"]))):
            read_stream(lines(name))
        return
    reply = read_stream(lines(name))
    assert (reply.content, reply.reasoning) == (want["content"], want["reasoning"])
    assert reply.chunks == want["pieces"]
    assert bool(reply.usage) is want["usage"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_lane_reader(name, monkeypatch):
    # A failed stream used to come back as a row of tokens at 0.0 tok/s; it must come back as the lane's error.
    want = EXPECTED[name]
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(name))
    row = lanes.stream_once("http://lane:1", "m", "hi")
    if "error" in want:
        assert want["error"] in row["error"]
        return
    assert "error" not in row
    assert row["tokens"] == want["pieces"]
    # Read at once, the pieces are a burst: under the 50 ms floor there is no rate.
    assert row["decode_tok_per_sec"] is None
    assert "floor" in row["decode_rate_note"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_contract_probe(name, monkeypatch):
    want = EXPECTED[name]
    monkeypatch.setattr(contract, "post_json", lambda *a, **k: _Stream(name))
    answer = contract.check_stream_usage(CTX)["answer"]
    if "error" in want:
        assert answer == "error"
    else:
        assert answer == ("yes" if want["usage"] else "no")
