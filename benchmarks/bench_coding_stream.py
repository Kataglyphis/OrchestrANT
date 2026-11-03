"""The coding lane's view of one streamed chat request."""

import json
import time


class StreamReply:
    """What one streamed chat request has delivered so far."""

    def __init__(self, started):
        """Start from the clock post_json started before sending."""
        self.started = started
        self.ttft = None
        self.parts, self.think_parts = [], []
        self.prompt_tokens = None
        self.completion_tokens = None
        self.finish = None

    def feed(self, line):
        """Take one SSE line; False once the server says [DONE]."""
        # A fault in an already-200 stream is a transport error, not an empty answer.
        if line.startswith("error:"):
            raise RuntimeError(f"server error in stream: {line[6:].strip()[:200]}")
        if not line.startswith("data:"):
            return True
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            return False
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return True
        if isinstance(chunk, dict) and chunk.get("error"):
            raise RuntimeError(
                f"server error in stream: {json.dumps(chunk['error'])[:200]}"
            )
        self._take_usage(chunk.get("usage"))
        choices = chunk.get("choices") or []
        if choices:
            self._take_choice(choices[0])
        return True

    def _take_usage(self, usage):
        if usage:
            self.prompt_tokens = usage.get("prompt_tokens")
            self.completion_tokens = usage.get(
                "completion_tokens", self.completion_tokens
            )

    def _take_choice(self, choice):
        delta = choice.get("delta") or {}
        self.finish = choice.get("finish_reason") or self.finish
        thought = delta.get("reasoning_content") or delta.get("reasoning")
        if thought:
            self._mark_first_token()
            self.think_parts.append(thought)
        piece = delta.get("content")
        if piece:
            self._mark_first_token()
            self.parts.append(piece)

    def _mark_first_token(self):
        if self.ttft is None:
            self.ttft = time.monotonic() - self.started
