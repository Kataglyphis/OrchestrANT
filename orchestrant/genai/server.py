"""``orchestrant-serve``: an OpenAI-compatible server over one GenAI model directory on the chain GenAI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from orchestrant.genai.engine import (
    GenaiEngine,
    GenaiError,
    GenerationOptions,
    check_request,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from orchestrant.genai.engine import GenerationResult

DEFAULT_PORT = 18200
BODY_LIMIT = 1 << 20
STATUS = {
    "invalid_options": 400,
    "chat_template_failed": 400,
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
    ) -> GenerationResult:
        """Generate one answer."""

    def cancel(self) -> None:
        """End the running generation."""


def options_from(body: dict[str, Any]) -> GenerationOptions:
    """Map an OpenAI request onto the contract's options; other keys are ignored, as OpenAI servers do."""
    defaults = GenerationOptions()
    budget = body.get(
        "max_completion_tokens", body.get("max_tokens", defaults.max_new_tokens)
    )
    return GenerationOptions(
        max_new_tokens=int(budget),
        temperature=float(body.get("temperature", defaults.temperature)),
        top_p=float(body.get("top_p", defaults.top_p)),
        top_k=int(body.get("top_k", defaults.top_k)),
    )


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
    """One engine behind the HTTP routes; the engine runs one generation at a time."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: Engine, model_id: str) -> None:
        """Bind ``address`` and serve ``engine`` under ``model_id``."""
        super().__init__(address, Handler)
        self.engine = engine
        self.model_id = model_id


class Handler(BaseHTTPRequestHandler):
    """``/v1/models`` and ``/v1/chat/completions``, streamed or whole."""

    @property
    def served(self) -> Server:
        """Return the server this request arrived at, with its engine."""
        return cast("Server", self.server)

    def do_GET(self) -> None:
        """Answer ``/v1/models`` with the one model this server holds."""
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
        if body.get("stream"):
            include = bool((body.get("stream_options") or {}).get("include_usage"))
            self._stream(messages, options, include_usage=include)
        else:
            self._complete(messages, options)

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
            body = json.loads(self.rfile.read(length))
            messages = list(body["messages"])
            options = options_from(body)
            check_request(messages, options)
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
        try:
            result = self.served.engine.generate(messages, options)
        except GenaiError as exc:
            self._error(STATUS.get(exc.kind, 500), exc.kind, exc.detail)
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
                messages, options, lambda piece: self._piece(envelope, piece)
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
            self._event({"error": {"type": exc.kind, "message": exc.detail}})
        except OSError:
            self.served.engine.cancel()
            return
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _piece(self, envelope: dict[str, Any], piece: str) -> None:
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
            # A client that went away stops the model too, rather than leaving it decoding for nobody.
            self.served.engine.cancel()

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load the model, then serve until interrupted."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        engine = GenaiEngine(args.model_dir, args.provider)
    except GenaiError as exc:
        sys.stderr.write(f"orchestrant-serve: {exc}\n")
        return 2
    model_id = args.model_id or args.model_dir.resolve().name
    with Server((args.host, args.port), engine, model_id) as server:
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
