"""``orchestrant-serve`` over a fake engine, and once over the tiny GenAI model."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from orchestrant.genai.engine import GenaiEngine, GenaiError
from orchestrant.genai.server import build_parser, options_from


if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.genai.conftest import FakeEngine, TinyModel

CHAT = {
    "model": "tiny",
    "messages": [{"role": "user", "content": "Capital of France?"}],
}


def post(url: str, body: dict[str, Any] | bytes) -> tuple[int, bytes]:
    """POST a JSON body and return the status and the raw answer, error statuses included."""
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 - a local test server
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - a local test server
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()


def events(raw: bytes) -> list[Any]:
    """Return the payloads of a server-sent stream, ``[DONE]`` as a string."""
    lines = [
        line.removeprefix("data: ")
        for line in raw.decode().splitlines()
        if line.startswith("data: ")
    ]
    return [line if line == "[DONE]" else json.loads(line) for line in lines]


def test_a_whole_answer_carries_the_text_the_usage_and_the_figures(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """The non-streamed answer is OpenAI's chat.completion object."""
    engine = fake_engine()
    status, raw = post(
        serve(engine) + "/v1/chat/completions",
        CHAT | {"max_tokens": 9, "temperature": 0.5},
    )
    answer = json.loads(raw)
    assert status == 200
    assert answer["choices"][0]["message"] == {
        "role": "assistant",
        "content": "The capital is Paris.",
    }
    assert answer["choices"][0]["finish_reason"] == "stop"
    assert answer["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    assert answer["timings"]["time_to_first_token_ms"] == 1.5
    assert engine.asked[0][1].max_new_tokens == 9
    assert engine.asked[0][1].temperature == 0.5


def test_a_stream_sends_each_piece_then_the_finish_the_usage_and_done(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """The chunks are OpenAI's, with the usage only when asked for."""
    body = CHAT | {"stream": True, "stream_options": {"include_usage": True}}
    status, raw = post(serve(fake_engine()) + "/v1/chat/completions", body)
    stream = events(raw)
    assert status == 200
    assert stream[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert [e["choices"][0]["delta"]["content"] for e in stream[1:4]] == [
        "The capital",
        " is",
        " Paris.",
    ]
    assert stream[4]["choices"][0]["finish_reason"] == "stop"
    assert stream[5]["usage"]["completion_tokens"] == 3
    assert stream[-1] == "[DONE]"


def test_a_stream_without_include_usage_sends_no_usage(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """A client that did not ask gets no extra chunk."""
    _, raw = post(
        serve(fake_engine()) + "/v1/chat/completions", CHAT | {"stream": True}
    )
    assert not any(isinstance(e, dict) and "usage" in e for e in events(raw))


def test_a_failure_inside_the_stream_is_an_error_event_not_an_answer(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """The headers are gone by then, so the error travels as an event."""
    engine = fake_engine(failure=GenaiError("boom", kind="generation_failed"))
    _, raw = post(serve(engine) + "/v1/chat/completions", CHAT | {"stream": True})
    assert {"error": {"type": "generation_failed", "message": "boom"}} in events(raw)


@pytest.mark.parametrize(
    ("body", "status", "kind"),
    [
        (b"{not json", 400, "invalid_request"),
        ({"model": "tiny"}, 400, "invalid_request"),
        ({"messages": []}, 400, "invalid_options"),
        (CHAT | {"max_tokens": 0}, 400, "invalid_options"),
        (CHAT | {"max_tokens": "many"}, 400, "invalid_request"),
    ],
)
def test_invalid_requests_get_a_400_that_names_them(
    serve: Callable[[Any], str],
    body: dict[str, Any] | bytes,
    status: int,
    kind: str,
    fake_engine: type[FakeEngine],
) -> None:
    """Nothing reaches the engine."""
    engine = fake_engine()
    got, raw = post(serve(engine) + "/v1/chat/completions", body)
    assert (got, json.loads(raw)["error"]["type"]) == (status, kind)
    assert engine.asked == []


def test_an_engine_failure_maps_to_its_http_status(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """A provider the library lacks is 503, any other failure 500."""
    unavailable = fake_engine(
        failure=GenaiError("no cuda", kind="provider_unavailable")
    )
    assert post(serve(unavailable) + "/v1/chat/completions", CHAT)[0] == 503
    broken = fake_engine(failure=GenaiError("boom", kind="generation_failed"))
    assert post(serve(broken) + "/v1/chat/completions", CHAT)[0] == 500


def test_models_lists_the_one_model_and_other_routes_are_404(
    serve: Callable[[Any], str], fake_engine: type[FakeEngine]
) -> None:
    """``/v1/models`` is what OpenAI clients probe first."""
    base = serve(fake_engine())
    with urllib.request.urlopen(base + "/v1/models", timeout=30) as response:  # noqa: S310 - a local test server
        assert json.loads(response.read())["data"][0]["id"] == "tiny"
    assert post(base + "/v1/completions", CHAT)[0] == 404
    status, raw = post(base + "/v1/embeddings", {"input": ["a"]})
    assert (status, json.loads(raw)["error"]["type"]) == (501, "unsupported")


def test_options_follow_openai_names_and_the_contracts_defaults() -> None:
    """``max_completion_tokens`` wins over the older ``max_tokens``."""
    options = options_from({"max_tokens": 5, "max_completion_tokens": 6})
    assert (options.max_new_tokens, options.temperature, options.top_k) == (6, 0.0, 50)


def test_the_server_listens_on_loopback_by_default() -> None:
    """A LAN address has to be asked for."""
    args = build_parser().parse_args(["--model-dir", "m"])
    assert (args.host, args.port) == ("127.0.0.1", 18200)


def test_the_tiny_model_streams_its_oracle_through_the_server(
    serve: Callable[[Any], str], tiny_model: TinyModel
) -> None:
    """The whole path, GenAI included."""
    body = {
        "messages": [{"role": "user", "content": tiny_model.prompt}],
        "max_tokens": len(tiny_model.ids),
        "stream": True,
    }
    _, raw = post(serve(GenaiEngine(tiny_model.path)) + "/v1/chat/completions", body)
    pieces = [
        e["choices"][0]["delta"].get("content", "")
        for e in events(raw)
        if isinstance(e, dict) and e.get("choices")
    ]
    assert "".join(pieces) == tiny_model.continuation
