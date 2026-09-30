#!/usr/bin/env python3
"""Translate Qwen's tool-call template into OpenAI `tool_calls`, for GenieX before v0.6.0.

Newer builds parse the template themselves, and the shim skips a message they
already parsed. Upstream is never streamed: a call is recognisable only at its
closing tag.

    python3 geniex_toolcall_shim.py --upstream http://localhost:18184 --port 18190
"""

import argparse
import json
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Non-greedy, so several calls in one message stay separate.
FUNCTION_RE = re.compile(r"<function=([A-Za-z0-9_.-]+)\s*>(.*?)</function>", re.DOTALL)
# <parameter=NAME> VALUE </parameter>
PARAMETER_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)\s*>(.*?)</parameter>", re.DOTALL
)
# The wrapper, which may or may not be closed when the model runs out of room.
TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?(?:</tool_call>|$)", re.DOTALL)
# Reasoning models emit a think block; some emit only the closing tag.
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text):
    """Remove <think> pairs, then a stray </think>: Qwen3.8 distills emit one with no opener."""
    text = THINK_RE.sub("", text)
    if "<think>" not in text:
        text = text.replace("</think>", "")
    return text


def parse_tool_calls(text):
    """(cleaned_text, tool_calls) in OpenAI shape; parameter values pass through verbatim."""
    if not text or "<function=" not in text:
        return strip_thinking(text or ""), []

    calls = []
    for name, body in FUNCTION_RE.findall(text):
        args = {}
        for pname, pvalue in PARAMETER_RE.findall(body):
            # Only the template's newline: indentation in handed code is meaningful.
            args[pname] = pvalue.strip("\n")
        calls.append(
            {
                "id": "call_%d" % (len(calls) + 1),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )

    if not calls:
        # A call cut off mid-template: nothing to run, and its markup must not reach the user.
        return strip_thinking(TOOL_CALL_BLOCK_RE.sub("", text)).strip(), []

    cleaned = TOOL_CALL_BLOCK_RE.sub("", text)
    # A bare <function=...> without the <tool_call> wrapper goes too.
    cleaned = FUNCTION_RE.sub("", cleaned)
    return strip_thinking(cleaned).strip(), calls


def convert_response(payload):
    """Rewrite a chat completion in place so its tool calls are visible."""
    for choice in payload.get("choices") or []:
        message = choice.get("message") or {}
        if message.get("tool_calls"):
            continue  # the server already did the work
        cleaned, calls = parse_tool_calls(message.get("content") or "")
        if not calls:
            if cleaned != (message.get("content") or ""):
                message["content"] = cleaned
            continue
        message["content"] = cleaned or None
        message["tool_calls"] = calls
        # Agent loops key off this: "stop" ends the turn without running the call.
        choice["finish_reason"] = "tool_calls"
    return payload


def as_sse(payload):
    """Re-emit a non-streamed completion as the stream the client asked for."""
    base = {
        "id": payload.get("id", "chatcmpl"),
        "object": "chat.completion.chunk",
        "created": payload.get("created", 0),
        "model": payload.get("model", ""),
    }
    out = []
    for choice in payload.get("choices") or []:
        idx = choice.get("index", 0)
        message = choice.get("message") or {}
        delta = {"role": "assistant"}
        if message.get("content"):
            delta["content"] = message["content"]
        if message.get("tool_calls"):
            delta["tool_calls"] = [
                dict(tc, index=i) for i, tc in enumerate(message["tool_calls"])
            ]
        out.append(
            dict(base, choices=[{"index": idx, "delta": delta, "finish_reason": None}])
        )
        out.append(
            dict(
                base,
                choices=[
                    {
                        "index": idx,
                        "delta": {},
                        "finish_reason": choice.get("finish_reason"),
                    }
                ],
            )
        )
    body = "".join("data: %s\n\n" % json.dumps(c) for c in out)
    return body + "data: [DONE]\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "http://localhost:18184"

    def log_message(self, *a):
        pass

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upstream(self, body=None, headers=None):
        req = urllib.request.Request(
            self.upstream + self.path, data=body, method=self.command
        )
        for k, v in (headers or {}).items():
            if k.lower() not in (
                "host",
                "content-length",
                "connection",
                "accept-encoding",
            ):
                req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=3600)

    def do_GET(self):
        try:
            with self._upstream(headers=dict(self.headers)) as r:
                self._send(r.status, r.read())
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as e:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(e)}))

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            request = None

        wants_stream = bool(request and request.get("stream"))
        if request is not None and wants_stream:
            # A tool call is only recognisable once its closing tag has arrived.
            forwarded = dict(request, stream=False)
            forwarded.pop("stream_options", None)
            raw = json.dumps(forwarded).encode()

        try:
            with self._upstream(raw, dict(self.headers)) as r:
                data = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
            return
        except Exception as e:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(e)}))
            return

        if not self.path.endswith("/chat/completions"):
            self._send(status, data)
            return
        try:
            payload = convert_response(json.loads(data))
        except (json.JSONDecodeError, AttributeError, TypeError):
            self._send(status, data)  # not a shape we understand: pass it through
            return
        if wants_stream:
            self._send(status, as_sse(payload), "text/event-stream")
        else:
            self._send(status, json.dumps(payload))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--upstream", default="http://localhost:18184")
    ap.add_argument("--port", type=int, default=18190)
    args = ap.parse_args()
    Handler.upstream = args.upstream.rstrip("/")
    print("tool-call shim: %s -> %s" % (args.port, Handler.upstream), flush=True)
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
