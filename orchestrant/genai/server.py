"""``orchestrant-serve``: an OpenAI-compatible server over one GenAI model directory on the chain GenAI."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import math
import os
import select
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import MSG_PEEK
from typing import TYPE_CHECKING, Any, Protocol, cast

from orchestrant.genai.engine import (
    GenaiEngine,
    GenaiError,
    GenerationOptions,
    check_request,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from socket import socket

    from orchestrant.genai.engine import GenerationResult

DEFAULT_PORT = 18200
BODY_LIMIT = 1 << 20
MAX_HANDLERS = 8
SOCKET_TIMEOUT = 30
# How long a request waits for the model before 503 busy; one answer of a large budget on a CPU takes minutes.
TURN_WAIT = 300.0
BUSY_RETRY_AFTER = 30
STATUS = {
    "invalid_options": 400,
    "chat_template_failed": 400,
    "context_length_exceeded": 400,
    "json_schema_unsupported": 400,
    "library_unavailable": 503,
    "provider_unavailable": 503,
}
LOG = logging.getLogger(__name__)


class Engine(Protocol):
    """What the server needs from an engine, so the tests can hand it a fake."""

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
        on_text: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Generate one answer; ``cancel`` ends only this one."""

    def cancel(self) -> None:
        """End the running generation."""


def invalid(detail: str) -> GenaiError:
    """Return the contract's error for a request no model can answer."""
    return GenaiError(detail, kind="invalid_options")


def budget_from(value: object) -> int:
    """Return ``max_tokens`` as an int; JSON's Infinity overflowed int() into a dropped connection."""
    if isinstance(value, float) and not math.isfinite(value):
        msg = f"max_tokens must be a finite integer, not {value}"
        raise invalid(msg)
    return int(cast("Any", value))


def seed_from(value: object) -> int | None:
    """Return OpenAI's ``seed``, refusing anything but an integer."""
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    msg = f"seed must be an integer, not {value!r}"
    raise invalid(msg)


def schema_from(response_format: object) -> str | None:
    """Return the JSON text of the schema OpenAI's ``response_format`` asks for, None for plain text."""
    if response_format is None:
        return None
    if not isinstance(response_format, dict):
        msg = "response_format must be an object"
        raise invalid(msg)
    kind = response_format.get("type")
    if kind == "text":
        return None
    if kind == "json_object":
        return json.dumps({"type": "object"})
    schema = (response_format.get("json_schema") or {}).get("schema")
    if kind != "json_schema" or not isinstance(schema, dict):
        msg = "response_format must be text, json_object, or json_schema with a schema object"
        raise invalid(msg)
    return json.dumps(schema)


def stop_from(value: object) -> tuple[str, ...]:
    """Return OpenAI's ``stop``, one string or a list of them, as a tuple."""
    if value is None:
        return ()
    stops = [value] if isinstance(value, str) else value
    if not isinstance(stops, list) or not all(isinstance(s, str) for s in stops):
        msg = "stop must be a string or a list of strings"
        raise invalid(msg)
    return tuple(stops)


def options_from(body: dict[str, Any]) -> GenerationOptions:
    """Map an OpenAI request onto the contract's options; other keys are ignored, as OpenAI servers do."""
    defaults = GenerationOptions()
    budget = body.get(
        "max_completion_tokens", body.get("max_tokens", defaults.max_new_tokens)
    )
    return GenerationOptions(
        max_new_tokens=budget_from(budget),
        temperature=float(body.get("temperature", defaults.temperature)),
        top_p=float(body.get("top_p", defaults.top_p)),
        top_k=int(body.get("top_k", defaults.top_k)),
        seed=seed_from(body.get("seed")),
        json_schema=schema_from(body.get("response_format")),
        stop=stop_from(body.get("stop")),
    )


def parse_request(
    raw: bytes,
) -> tuple[list[dict[str, Any]], GenerationOptions, dict[str, Any]]:
    """Decode and check one chat-completions body; raises GenaiError, or the decode errors the handler maps to 400."""
    body = json.loads(raw)
    messages = list(body["messages"])
    options = options_from(body)
    check_request(messages, options)
    return messages, options, body


def usage(result: GenerationResult) -> dict[str, int]:
    """Return the OpenAI usage block of one result."""
    return {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.prompt_tokens + result.completion_tokens,
    }


def timings(result: GenerationResult) -> dict[str, float]:
    """Return the contract's speed figures, which OpenAI's format has no field for."""
    return {
        "time_to_first_token_ms": result.time_to_first_token_ms,
        "decode_tokens_per_second": result.decode_tokens_per_second,
    }


class Server(ThreadingHTTPServer):
    """One engine behind the HTTP routes; the engine runs one generation at a time, at most MAX_HANDLERS requests are handled."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        engine: Engine,
        model_id: str,
        *,
        cors_origin: str | None = None,
        api_key: str | None = None,
        turn_wait: float = TURN_WAIT,
    ) -> None:
        """Bind ``address`` and serve ``engine`` under ``model_id``; ``api_key`` requires that Bearer token."""
        super().__init__(address, Handler)
        self.engine = engine
        self.model_id = model_id
        self.cors_origin = cors_origin
        self.api_key = api_key
        self._slots = threading.BoundedSemaphore(MAX_HANDLERS)
        # The server's, not the engine's: only a lock it holds can time out and answer busy.
        self.turn = threading.Lock()
        self.turn_wait = turn_wait

    def process_request(
        self, request: socket | tuple[bytes, socket], client_address: object
    ) -> None:
        """Wait for a free handler slot before starting a thread, so a flood queues instead of spawning threads."""
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self, request: socket | tuple[bytes, socket], client_address: object
    ) -> None:
        """Handle one request, then free its slot."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    """``/v1/models`` and ``/v1/chat/completions``, streamed or whole."""

    timeout = SOCKET_TIMEOUT

    @property
    def served(self) -> Server:
        """Return the server this request arrived at, with its engine."""
        return cast("Server", self.server)

    def end_headers(self) -> None:
        """Add the CORS origin to every answer when ``--cors-origin`` named one."""
        if self.served.cors_origin:
            self.send_header("Access-Control-Allow-Origin", self.served.cors_origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        """Answer a browser's preflight; it carries no credentials, so it needs none."""
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        if self.served.cors_origin:
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Authorization, Content-Type"
            )
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self) -> bool:
        key = self.served.api_key
        if not key:
            return True
        given = self.headers.get("Authorization", "")
        if hmac.compare_digest(given.encode(), f"Bearer {key}".encode()):
            return True
        self._error(401, "unauthorized", "a valid Bearer token is required")
        return False

    def do_GET(self) -> None:
        """Answer ``/v1/models`` with the one model this server holds."""
        if not self._authorized():
            return
        if self.path.rstrip("/") != "/v1/models":
            self._error(404, "not_found", f"no route {self.path}")
            return
        model = {
            "id": self.served.model_id,
            "object": "model",
            "owned_by": "orchestrant",
        }
        self._json(200, {"object": "list", "data": [model]})

    def do_POST(self) -> None:
        """Answer a chat completion; embeddings are not served by this lane."""
        if not self._authorized():
            return
        route = self.path.rstrip("/")
        if route == "/v1/embeddings":
            self._error(501, "unsupported", "this lane serves no embeddings")
            return
        if route != "/v1/chat/completions":
            self._error(404, "not_found", f"no route {self.path}")
            return
        request = self._request()
        if request is None:
            return
        messages, options, body = request
        if body.get("model") not in (None, "", self.served.model_id):
            self._error(
                404, "model_not_found", f"this server holds {self.served.model_id}"
            )
            return
        if not self.served.turn.acquire(timeout=self.served.turn_wait):
            self._busy()
            return
        try:
            if body.get("stream"):
                include = bool((body.get("stream_options") or {}).get("include_usage"))
                self._stream(messages, options, include_usage=include)
            else:
                self._complete(messages, options)
        finally:
            self.served.turn.release()

    def _busy(self) -> None:
        data = json.dumps(
            {
                "error": {
                    "type": "busy",
                    "message": f"the model is busy; no turn came in {self.served.turn_wait:g} s",
                }
            }
        ).encode()
        self.send_response(503)
        self.send_header("Retry-After", str(BUSY_RETRY_AFTER))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the name the base class fixes
        """Send the access log to logging rather than stderr."""
        LOG.info("%s %s", self.address_string(), format % args)

    def _request(
        self,
    ) -> tuple[list[dict[str, Any]], GenerationOptions, dict[str, Any]] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= BODY_LIMIT:
            self._error(
                400, "invalid_request", f"a body of 1 to {BODY_LIMIT} bytes is required"
            )
            return None
        try:
            messages, options, body = parse_request(self.rfile.read(length))
        except GenaiError as exc:
            self._error(400, exc.kind, exc.detail)
            return None
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            self._error(400, "invalid_request", f"not a chat completion request: {exc}")
            return None
        return messages, options, body

    def _complete(
        self, messages: list[dict[str, Any]], options: GenerationOptions
    ) -> None:
        gone = threading.Event()

        def watch(_piece: str) -> None:
            if client_left(self.connection):
                gone.set()

        try:
            result = self.served.engine.generate(messages, options, watch, gone)
        except GenaiError as exc:
            self._error(STATUS.get(exc.kind, 500), exc.kind, exc.detail)
            return
        except RuntimeError as exc:
            self._error(500, "generation_failed", str(exc))
            return
        choice = {
            "index": 0,
            "message": {"role": "assistant", "content": result.text},
            "finish_reason": result.finish_reason,
        }
        answer = self._envelope("chat.completion") | {
            "choices": [choice],
            "usage": usage(result),
            "timings": timings(result),
        }
        self._json(200, answer)

    def _stream(
        self,
        messages: list[dict[str, Any]],
        options: GenerationOptions,
        *,
        include_usage: bool,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        envelope = self._envelope("chat.completion.chunk")
        gone = threading.Event()
        try:
            self._event(
                envelope
                | {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ]
                }
            )
            result = self.served.engine.generate(
                messages,
                options,
                lambda piece: self._piece(envelope, piece, gone),
                gone,
            )
            self._event(
                envelope
                | {
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": result.finish_reason}
                    ],
                    "timings": timings(result),
                }
            )
            if include_usage:
                self._event(envelope | {"choices": [], "usage": usage(result)})
        except GenaiError as exc:
            self._end_stream({"error": {"type": exc.kind, "message": exc.detail}})
            return
        except RuntimeError as exc:
            self._end_stream(
                {"error": {"type": "generation_failed", "message": str(exc)}}
            )
            return
        except OSError:
            return
        self._end_stream(None)

    def _end_stream(self, error: dict[str, Any] | None) -> None:
        try:
            if error:
                self._event(error)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            LOG.info("%s left before the stream ended", self.address_string())

    def _piece(
        self, envelope: dict[str, Any], piece: str, gone: threading.Event
    ) -> None:
        try:
            self._event(
                envelope
                | {
                    "choices": [
                        {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                    ]
                }
            )
        except OSError:
            # Only this request's run stops: an engine-wide cancel ended whichever client ran next.
            gone.set()

    def _event(self, payload: dict[str, Any]) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def _envelope(self, kind: str) -> dict[str, Any]:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": kind,
            "created": int(time.time()),
            "model": self.served.model_id,
        }

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, kind: str, message: str) -> None:
        self._json(status, {"error": {"type": kind, "message": message}})


def client_left(conn: socket) -> bool:
    """Whether the client closed its end: readable with nothing to read; bytes it sent early are no close."""
    try:
        readable, _, _ = select.select([conn], [], [], 0)
        return bool(readable) and conn.recv(1, MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def build_parser() -> argparse.ArgumentParser:
    """Return the command line of ``orchestrant-serve``."""
    parser = argparse.ArgumentParser(prog="orchestrant-serve", description=__doc__)
    parser.add_argument(
        "--model-dir", required=True, type=Path, help="a GenAI model directory"
    )
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "dml"])
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="127.0.0.1 by default; a LAN address exposes the model",
    )
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument(
        "--model-id",
        help="the name /v1/models reports; the directory's name by default",
    )
    parser.add_argument(
        "--cors-origin", help="the one browser origin allowed to call the server"
    )
    parser.add_argument(
        "--api-key-env",
        help="the environment variable holding the Bearer token every request must carry",
    )
    return parser


def api_key_from(name: str | None) -> str | None:
    """Return the token the variable ``name`` holds; a named but empty variable must not leave the server open."""
    if not name:
        return None
    key = os.environ.get(name, "")
    if not key:
        msg = f"--api-key-env names {name}, which is not set"
        raise GenaiError(msg, kind="invalid_options")
    return key


def main(argv: Sequence[str] | None = None) -> int:
    """Load the model, then serve until interrupted."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        api_key = api_key_from(args.api_key_env)
        engine = GenaiEngine(args.model_dir, args.provider)
    except GenaiError as exc:
        sys.stderr.write(f"orchestrant-serve: {exc}\n")
        return 2
    model_id = args.model_id or args.model_dir.resolve().name
    address = (args.host, args.port)
    with Server(
        address, engine, model_id, cors_origin=args.cors_origin, api_key=api_key
    ) as server:
        LOG.info(
            "serving %s on http://%s:%d/v1",
            model_id,
            args.host,
            server.server_address[1],
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            LOG.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
