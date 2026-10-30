"""The family's text contract over the chain GenAI wheel: messages in, pieces out, a cancel from any thread."""

from __future__ import annotations

import importlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import ModuleType


class Generator(Protocol):
    """The part of ``onnxruntime_genai.Generator`` the decode loop calls; the wheel ships no annotations."""

    def is_done(self) -> bool:
        """Return whether an end token or the length budget stopped it."""

    def generate_next_token(self) -> None:
        """Decode one token."""

    def get_next_tokens(self) -> Sequence[int]:
        """Return the token each sequence just decoded."""


ROLES = frozenset({"system", "user", "assistant", "tool"})
PROVIDERS: dict[str, str | None] = {"cpu": None, "cuda": "cuda", "dml": "dml"}


class GenaiError(RuntimeError):
    """One failure of the text contract; ``kind`` names it and is never an empty answer."""

    def __init__(self, detail: str, *, kind: str) -> None:
        """Keep the kind for callers that map it, such as the server's HTTP status."""
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class GenerationOptions:
    """Temperature 0, the default, decodes greedily and ignores ``top_p`` and ``top_k``."""

    max_new_tokens: int = 128
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 50


@dataclass
class GenerationResult:
    """The text, its token ids, why it ended and how fast it came."""

    text: str = ""
    tokens: list[int] = field(default_factory=list)
    finish_reason: str = "length"
    prompt_tokens: int = 0
    time_to_first_token_ms: float = 0.0
    decode_tokens_per_second: float = 0.0

    @property
    def completion_tokens(self) -> int:
        """Return the number of generated tokens, the end token included."""
        return len(self.tokens)


def check_request(
    messages: Sequence[dict[str, Any]], options: GenerationOptions
) -> None:
    """Refuse what no model can answer, before anything is decoded."""
    problem = ""
    if not messages:
        problem = "no messages"
    elif options.max_new_tokens <= 0:
        problem = f"max_new_tokens is {options.max_new_tokens}"
    elif any(m.get("role") not in ROLES for m in messages):
        problem = f"a role is not one of {sorted(ROLES)}"
    elif any(not isinstance(m.get("content"), str) for m in messages):
        problem = "a message has no text content"
    if problem:
        raise GenaiError(problem, kind="invalid_options")


def end_tokens(model_dir: Path) -> frozenset[int]:
    """Return the end tokens ``genai_config.json`` names; GenAI emits the one that stops it."""
    config = json.loads((model_dir / "genai_config.json").read_text(encoding="utf-8"))
    ids = config.get("model", {}).get("eos_token_id", [])
    return frozenset([ids] if isinstance(ids, int) else ids)


def decode_rate(tokens: int, seconds: float) -> float:
    """Return the tokens per second after the first, 0 when fewer than two arrived."""
    if tokens < 2 or seconds <= 0:
        return 0.0
    return (tokens - 1) / seconds


def import_genai() -> ModuleType:
    """Import the GenAI wheel, or fail with the contract's library error."""
    try:
        return importlib.import_module("onnxruntime_genai")
    except ImportError as exc:
        msg = f"onnxruntime_genai: {exc}"
        raise GenaiError(msg, kind="library_unavailable") from exc


class GenaiEngine:
    """One GenAI model directory; one generation runs at a time and others wait."""

    def __init__(
        self, model_dir: str | Path, provider: str = "cpu", og: ModuleType | None = None
    ) -> None:
        """Load the model on ``provider`` (``cpu``, ``cuda`` or ``dml``)."""
        self.model_dir = Path(model_dir)
        if provider not in PROVIDERS:
            msg = f"{provider!r} is not one of {sorted(PROVIDERS)}"
            raise GenaiError(msg, kind="provider_unavailable")
        if not (self.model_dir / "genai_config.json").is_file():
            msg = f"no genai_config.json in {self.model_dir}"
            raise GenaiError(msg, kind="model_load_failed")
        self._og = og or import_genai()
        self._model = self._load(provider)
        self._tokenizer = self._og.Tokenizer(self._model)
        self._end = end_tokens(self.model_dir)
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def _load(self, provider: str) -> object:
        config = self._og.Config(str(self.model_dir))
        config.clear_providers()
        name = PROVIDERS[provider]
        try:
            if name:
                config.append_provider(name)
            return self._og.Model(config)
        except RuntimeError as exc:
            kind = (
                "provider_unavailable"
                if name and name in str(exc).lower()
                else "model_load_failed"
            )
            raise GenaiError(str(exc), kind=kind) from exc

    def cancel(self) -> None:
        """End the running generation after its current token; its finish reason becomes ``cancelled``."""
        self._cancel.set()

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        """Apply the model's chat template, then stream each decoded piece to ``on_text``."""
        options = options or GenerationOptions()
        check_request(messages, options)
        with self._lock:
            self._cancel.clear()
            # The clock starts before the prompt goes in, so the first token's time includes the prefill.
            start = time.perf_counter()
            prompt = self._tokenizer.encode(self._template(messages))
            generator = self._generator(prompt, options)
            result = GenerationResult(prompt_tokens=len(prompt))
            return self._decode(generator, result, start, on_text)

    def _template(self, messages: Sequence[dict[str, Any]]) -> str:
        chat = json.dumps(
            [{"role": m["role"], "content": m["content"]} for m in messages]
        )
        try:
            return self._tokenizer.apply_chat_template(chat, add_generation_prompt=True)
        except RuntimeError as exc:
            raise GenaiError(str(exc), kind="chat_template_failed") from exc

    def _generator(
        self, prompt: Sequence[int], options: GenerationOptions
    ) -> Generator:
        params = self._og.GeneratorParams(self._model)
        search: dict[str, Any] = {"max_length": len(prompt) + options.max_new_tokens}
        if options.temperature > 0:
            search |= {
                "do_sample": True,
                "temperature": options.temperature,
                "top_p": options.top_p,
                "top_k": options.top_k,
            }
        else:
            search["do_sample"] = False
        params.set_search_options(**search)
        generator = self._og.Generator(self._model, params)
        generator.append_tokens(prompt)
        return generator

    def _decode(
        self,
        generator: Generator,
        result: GenerationResult,
        start: float,
        on_text: Callable[[str], None] | None,
    ) -> GenerationResult:
        stream = self._tokenizer.create_stream()
        first = start
        while not generator.is_done():
            if self._cancel.is_set():
                result.finish_reason = "cancelled"
                break
            generator.generate_next_token()
            token = int(generator.get_next_tokens()[0])
            result.tokens.append(token)
            if len(result.tokens) == 1:
                first = time.perf_counter()
                result.time_to_first_token_ms = (first - start) * 1000
            piece = stream.decode(token)
            if piece:
                result.text += piece
                if on_text:
                    on_text(piece)
        if (
            result.finish_reason != "cancelled"
            and result.tokens
            and result.tokens[-1] in self._end
        ):
            result.finish_reason = "stop"
        result.decode_tokens_per_second = decode_rate(
            len(result.tokens), time.perf_counter() - first
        )
        return result
