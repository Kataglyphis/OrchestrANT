"""``orchestrant-serve`` over a fake engine, and once over the tiny GenAI model."""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from orchestrant.genai import server as serve_module
from orchestrant.genai.engine import (
    GenaiEngine,
    GenaiError,
    GenerationResult,
)
from orchestrant.genai.server import (
    MAX_HANDLERS,
    SOCKET_TIMEOUT,
    api_key_from,
    build_parser,
    options_from,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from orchestrant.genai.engine import (
        GenerationOptions,
    )
    from tests.unit.genai.conftest import FakeEngine, TinyModel

CHAT = {
    "model": "tiny",
    "messages": [{"role": "user", "content": "Capital of France?"}],
}


def call(
    url: str,
    body: dict[str, Any] | bytes | None = None,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    """Send one request and return the status, the raw answer and the headers, error statuses included."""
    data = body if isinstance(body, bytes | None) else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 - a local test server
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} | (headers or {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - a local test server
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read(), dict(exc.headers)


def post(url: str, body: dict[str, Any] | bytes) -> tuple[int, bytes]:
    """POST a JSON body and return the status and the raw answer, error statuses included."""
    status, raw, _ = call(url, body)
    return status, raw


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


def kind_of(raw: bytes) -> str:
    """Return the error type of an error answer."""
    return json.loads(raw)["error"]["type"]


def test_seed_response_format_and_stop_reach_the_engine(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """OpenAI's names arrive at the engine as the contract's options."""
    engine = fake_engine()
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    body = CHAT | {
        "seed": 11,
        "stop": ["\n\n"],
        "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
    }
    assert post(serve(engine) + "/v1/chat/completions", body)[0] == 200
    options = engine.asked[0][1]
    assert (options.seed, options.stop) == (11, ("\n\n",))
    assert json.loads(options.json_schema or "") == schema


def test_an_unknown_model_is_404(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """The server holds one model and says so rather than answering with it."""
    engine = fake_engine()
    status, raw = post(
        serve(engine) + "/v1/chat/completions", CHAT | {"model": "gpt-4"}
    )
    assert (status, kind_of(raw)) == (404, "model_not_found")
    assert engine.asked == []


def test_a_schema_the_engine_cannot_guide_is_400_json_schema_unsupported(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """The engine's refusal keeps its name."""
    engine = fake_engine(
        failure=GenaiError("no llguidance", kind="json_schema_unsupported")
    )
    status, raw = post(serve(engine) + "/v1/chat/completions", CHAT)
    assert (status, kind_of(raw)) == (400, "json_schema_unsupported")


def test_a_json_schema_request_answers_valid_json_or_400(
    serve: Callable[..., str], schema_engine: tuple[GenaiEngine, bool]
) -> None:
    """GenAI with llguidance answers the schema; without it the answer is 400 json_schema_unsupported, never free text."""
    engine, guided = schema_engine
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", "maxLength": 3}},
        "required": ["a"],
        "additionalProperties": False,
        "x-guidance": {"whitespace_flexible": False},
    }
    body = {
        "messages": [{"role": "user", "content": "abc"}],
        "max_tokens": 32,
        "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
    }
    status, raw = post(serve(engine) + "/v1/chat/completions", body)
    if not guided:
        assert (status, kind_of(raw)) == (400, "json_schema_unsupported")
        return
    assert status == 200
    answer = json.loads(json.loads(raw)["choices"][0]["message"]["content"])
    assert set(answer) == {"a"}
    assert len(answer["a"]) <= 3


def test_an_infinite_budget_is_400_invalid_options(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """JSON's Infinity used to overflow int() and drop the connection."""
    raw_body = (
        b'{"messages": [{"role": "user", "content": "a"}], "max_tokens": Infinity}'
    )
    status, raw = post(serve(fake_engine()) + "/v1/chat/completions", raw_body)
    assert (status, kind_of(raw)) == (400, "invalid_options")


def test_a_budget_over_the_context_is_400_context_length_exceeded(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """The engine's refusal maps to 400 with its name."""
    engine = fake_engine(
        failure=GenaiError("over the context", kind="context_length_exceeded")
    )
    status, raw = post(serve(engine) + "/v1/chat/completions", CHAT)
    assert (status, kind_of(raw)) == (400, "context_length_exceeded")


def test_the_tiny_models_context_refuses_a_huge_budget_with_400(
    serve: Callable[..., str], tiny_model: TinyModel
) -> None:
    """The whole path: the 512-token context of the real model."""
    body = {
        "messages": [{"role": "user", "content": tiny_model.prompt}],
        "max_tokens": 10**9,
    }
    status, raw = post(
        serve(GenaiEngine(tiny_model.path)) + "/v1/chat/completions", body
    )
    assert (status, kind_of(raw)) == (400, "context_length_exceeded")


def test_a_genai_runtime_error_is_500_generation_failed(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """An unwrapped native error is still named."""
    engine = fake_engine(failure=RuntimeError("native decode failed"))
    status, raw = post(serve(engine) + "/v1/chat/completions", CHAT)
    assert (status, kind_of(raw)) == (500, "generation_failed")


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (GenaiError("boom", kind="generation_failed"), "generation_failed"),
        (RuntimeError("native decode failed"), "generation_failed"),
    ],
)
def test_a_failed_stream_ends_with_an_error_event_and_done(
    serve: Callable[..., str],
    fake_engine: type[FakeEngine],
    failure: Exception,
    kind: str,
) -> None:
    """A client waiting for [DONE] is never left hanging after the error."""
    engine = fake_engine(failure=failure)
    _, raw = post(serve(engine) + "/v1/chat/completions", CHAT | {"stream": True})
    stream = events(raw)
    assert stream[-2]["error"]["type"] == kind
    assert stream[-1] == "[DONE]"


def stream_then_reset(base: str) -> None:
    """Start a stream, read until the engine sent a piece, then reset the connection."""
    port = int(base.rsplit(":", 1)[1])
    body = json.dumps(CHAT | {"stream": True}).encode()
    head = (
        "POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(head.encode() + body)
        seen = b""
        while b'"content"' not in seen:
            chunk = client.recv(4096)
            assert chunk, seen
            seen += chunk
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


def wait_for(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    """Poll ``predicate`` until it holds or the time is up."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_one_client_leaving_does_not_cancel_the_next_clients_answer(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """Client A resets mid-stream; client B's answer still finishes with stop."""
    engine = fake_engine(pieces=["x"] * 200, delay=0.01)
    base = serve(engine)
    stream_then_reset(base)
    assert wait_for(lambda: len(engine.results) == 1)
    assert engine.results[0].finish_reason == "cancelled"
    engine.delay = 0.0
    status, raw = post(base + "/v1/chat/completions", CHAT)
    assert status == 200
    assert json.loads(raw)["choices"][0]["finish_reason"] == "stop"


class GateEngine:
    """Holds every caller until released and counts how many got in."""

    def __init__(self) -> None:
        """Start closed."""
        self.release = threading.Event()
        self.inside = 0
        self._lock = threading.Lock()

    def generate(
        self,
        _messages: Sequence[dict[str, Any]],
        _options: GenerationOptions | None = None,
        _on_text: Callable[[str], None] | None = None,
        _cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Wait for the release, then answer."""
        with self._lock:
            self.inside += 1
        self.release.wait(timeout=20)
        return GenerationResult(text="ok", tokens=[1], finish_reason="stop")

    def cancel(self) -> None:
        """Nothing runs that a cancel could end."""


def test_at_most_eight_requests_are_handled_at_once(
    serve: Callable[..., str],
) -> None:
    """Twelve concurrent clients: eight are inside the engine, the rest queue, and all are answered."""
    engine = GateEngine()
    base = serve(engine)
    statuses: list[int] = []
    clients = [
        threading.Thread(
            target=lambda: statuses.append(post(base + "/v1/chat/completions", CHAT)[0])
        )
        for _ in range(12)
    ]
    for client in clients:
        client.start()
    assert wait_for(lambda: engine.inside == MAX_HANDLERS)
    time.sleep(0.3)
    assert engine.inside == MAX_HANDLERS == 8
    engine.release.set()
    for client in clients:
        client.join(timeout=30)
    assert statuses == [200] * 12


def test_the_socket_timeout_is_thirty_seconds() -> None:
    """A client that stops sending frees its handler slot after 30 s."""
    assert serve_module.Handler.timeout == SOCKET_TIMEOUT == 30


def test_an_idle_client_is_dropped_after_the_socket_timeout(
    serve: Callable[..., str],
    fake_engine: type[FakeEngine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeout is what frees the slot, shown at 0.2 s instead of 30."""
    monkeypatch.setattr(serve_module.Handler, "timeout", 0.2)
    port = int(serve(fake_engine()).rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\n")
        started = time.monotonic()
        assert client.recv(1024) == b""
        assert time.monotonic() - started < 5


def test_options_answers_204_with_cors_headers_for_the_named_origin(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """A browser's preflight passes, and the answer after it carries the origin too."""
    origin = "http://localhost:5173"
    base = serve(fake_engine(), cors_origin=origin)
    status, _, headers = call(base + "/v1/chat/completions", method="OPTIONS")
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == origin
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    _, _, answered = call(base + "/v1/chat/completions", CHAT)
    assert answered["Access-Control-Allow-Origin"] == origin


def test_without_cors_origin_no_origin_is_allowed(
    serve: Callable[..., str], fake_engine: type[FakeEngine]
) -> None:
    """OPTIONS still answers 204, without any CORS header."""
    status, _, headers = call(
        serve(fake_engine()) + "/v1/chat/completions", method="OPTIONS"
    )
    assert status == 204
    assert "Access-Control-Allow-Origin" not in headers


def test_a_wrong_bearer_token_is_401_under_api_key_env(
    serve: Callable[..., str],
    fake_engine: type[FakeEngine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token comes from the variable --api-key-env names; a wrong or missing one gets 401."""
    monkeypatch.setenv("ORCHESTRANT_TEST_KEY", "s3cret")
    args = build_parser().parse_args(
        ["--model-dir", "m", "--api-key-env", "ORCHESTRANT_TEST_KEY"]
    )
    engine = fake_engine()
    base = serve(engine, api_key=api_key_from(args.api_key_env))
    url = base + "/v1/chat/completions"
    wrong = call(url, CHAT, headers={"Authorization": "Bearer guess"})
    assert (wrong[0], kind_of(wrong[1])) == (401, "unauthorized")
    assert call(url, CHAT)[0] == 401
    assert call(base + "/v1/models", method="GET")[0] == 401
    assert engine.asked == []
    right = call(url, CHAT, headers={"Authorization": "Bearer s3cret"})
    assert right[0] == 200


def test_an_api_key_env_naming_an_unset_variable_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open server must not start where a protected one was asked for."""
    monkeypatch.delenv("ORCHESTRANT_TEST_KEY", raising=False)
    with pytest.raises(GenaiError, match="ORCHESTRANT_TEST_KEY"):
        api_key_from("ORCHESTRANT_TEST_KEY")
    assert api_key_from(None) is None
