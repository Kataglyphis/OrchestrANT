"""The family's text contract over the chain GenAI wheel: messages in, pieces out, a cancel from any thread."""

from __future__ import annotations

import importlib
import json
import math
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


SEED_LIMIT = 2**31


@dataclass(frozen=True)
class GenerationOptions:
    """Temperature 0, the default, decodes greedily and ignores ``top_p``, ``top_k`` and ``seed``.

    ``json_schema`` is the schema's JSON text; ``stop`` ends the answer before the first stop string.
    """

    max_new_tokens: int = 128
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 50
    seed: int | None = None
    json_schema: str | None = None
    stop: tuple[str, ...] = ()


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


def message_problem(messages: Sequence[dict[str, Any]]) -> str:
    """Return why no model can answer these messages, or an empty string."""
    if not messages:
        return "no messages"
    if any(m.get("role") not in ROLES for m in messages):
        return f"a role is not one of {sorted(ROLES)}"
    if any(not isinstance(m.get("content"), str) for m in messages):
        return "a message has no text content"
    return ""


def schema_problem(schema: str | None) -> str:
    """Return why ``schema`` is no JSON schema object, or an empty string."""
    if schema is None:
        return ""
    try:
        parsed = json.loads(schema)
    except ValueError as exc:
        return f"json_schema is not JSON: {exc}"
    return "" if isinstance(parsed, dict) else "json_schema is not a JSON object"


def option_problem(options: GenerationOptions) -> str:
    """Return why these options cannot drive a decode, or an empty string."""
    if options.max_new_tokens <= 0:
        return f"max_new_tokens is {options.max_new_tokens}"
    if not all(math.isfinite(v) for v in (options.temperature, options.top_p)):
        return "temperature and top_p must be finite"
    seed = options.seed
    if seed is not None and (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0 <= seed < SEED_LIMIT
    ):
        return f"seed {seed!r} is not an integer from 0 to {SEED_LIMIT - 1}"
    if any(not isinstance(s, str) or not s for s in options.stop):
        return "every stop entry must be a non-empty string"
    return schema_problem(options.json_schema)


def check_request(
    messages: Sequence[dict[str, Any]], options: GenerationOptions
) -> None:
    """Refuse what no model can answer, before anything is decoded."""
    problem = message_problem(messages) or option_problem(options)
    if problem:
        raise GenaiError(problem, kind="invalid_options")


def end_tokens(model_dir: Path) -> frozenset[int]:
    """Return the end tokens ``genai_config.json`` names; GenAI emits the one that stops it."""
    config = json.loads((model_dir / "genai_config.json").read_text(encoding="utf-8"))
    ids = config.get("model", {}).get("eos_token_id", [])
    return frozenset([ids] if isinstance(ids, int) else ids)


def context_length(model_dir: Path) -> int | None:
    """Return the context ``genai_config.json`` names, or None when it names none."""
    config = json.loads((model_dir / "genai_config.json").read_text(encoding="utf-8"))
    length = config.get("model", {}).get("context_length")
    return length if isinstance(length, int) and length > 0 else None


class StopScan:
    """Holds back text that could still become a stop string, so no streamed piece carries part of one."""

    def __init__(self, stops: Sequence[str]) -> None:
        """Watch for each of ``stops``."""
        self._stops = tuple(stops)
        self._held = ""

    def feed(self, piece: str) -> tuple[str, bool]:
        """Return the text safe to emit and whether a stop string was reached."""
        text = self._held + piece
        hits = [i for i in (text.find(s) for s in self._stops) if i >= 0]
        if hits:
            self._held = ""
            return text[: min(hits)], True
        keep = max(
            (n for s in self._stops for n in range(1, len(s)) if text.endswith(s[:n])),
            default=0,
        )
        self._held = text[len(text) - keep :]
        return text[: len(text) - keep], False

    def flush(self) -> str:
        """Return what was held back once no stop string can follow."""
        held, self._held = self._held, ""
        return held


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
        self._context = context_length(self.model_dir)
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
        cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Apply the model's chat template, then stream each decoded piece to ``on_text``.

        ``cancel`` ends only this run, so one caller's cancel never reaches the next caller's run.
        """
        options = options or GenerationOptions()
        check_request(messages, options)
        with self._lock:
            self._cancel.clear()
            # The clock starts before the prompt goes in, so the first token's time includes the prefill.
            start = time.perf_counter()
            prompt = self._tokenizer.encode(self._template(messages))
            self._check_context(len(prompt), options.max_new_tokens)
            generator = self._generator(prompt, options)
            result = GenerationResult(prompt_tokens=len(prompt))
            run = Run(generator, StopScan(options.stop), on_text, cancel)
            return self._decode(run, result, start)

    def _check_context(self, prompt_tokens: int, budget: int) -> None:
        if self._context and prompt_tokens + budget > self._context:
            msg = (
                f"{prompt_tokens} prompt tokens plus max_new_tokens {budget} "
                f"exceed the model's context of {self._context}"
            )
            raise GenaiError(msg, kind="context_length_exceeded")

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
        params.set_search_options(**search_options(len(prompt), options))
        if options.json_schema is not None:
            guide(params, options.json_schema)
        # GenAI refuses a prompt plus budget over the model's context here; unwrapped, it escaped the server's error mapping.
        try:
            generator = self._og.Generator(self._model, params)
            generator.append_tokens(prompt)
        except RuntimeError as exc:
            guided = options.json_schema is not None and "guidance" in str(exc).lower()
            kind = "json_schema_unsupported" if guided else "invalid_options"
            raise GenaiError(str(exc), kind=kind) from exc
        return generator

    def _cancelled(self, run: Run) -> bool:
        return self._cancel.is_set() or (run.cancel is not None and run.cancel.is_set())

    def _decode(
        self, run: Run, result: GenerationResult, start: float
    ) -> GenerationResult:
        stream = self._tokenizer.create_stream()
        first = start
        stopped = False
        while not stopped and not run.generator.is_done():
            if self._cancelled(run):
                result.finish_reason = "cancelled"
                break
            token = next_token(run.generator)
            result.tokens.append(token)
            if len(result.tokens) == 1:
                first = time.perf_counter()
                result.time_to_first_token_ms = (first - start) * 1000
            text, stopped = run.scan.feed(stream.decode(token))
            run.emit(result, text)
        if not stopped:
            run.emit(result, run.scan.flush())
        if stopped or (
            result.finish_reason != "cancelled"
            and result.tokens
            and result.tokens[-1] in self._end
        ):
            result.finish_reason = "stop"
        result.decode_tokens_per_second = decode_rate(
            len(result.tokens), time.perf_counter() - first
        )
        return result


@dataclass
class Run:
    """One generation's decode state: its generator, stop scan, sink and own cancel."""

    generator: Generator
    scan: StopScan
    on_text: Callable[[str], None] | None
    cancel: threading.Event | None

    def emit(self, result: GenerationResult, text: str) -> None:
        """Append ``text`` to the answer and stream it."""
        if text:
            result.text += text
            if self.on_text:
                self.on_text(text)


def search_options(prompt_tokens: int, options: GenerationOptions) -> dict[str, Any]:
    """Return GenAI's search options: greedy at temperature 0, else sampling, seeded when asked."""
    search: dict[str, Any] = {"max_length": prompt_tokens + options.max_new_tokens}
    if options.temperature > 0:
        search |= {
            "do_sample": True,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "top_k": options.top_k,
        }
    else:
        search["do_sample"] = False
    if options.seed is not None:
        search["random_seed"] = options.seed
    return search


def guide(params: object, schema: str) -> None:
    """Constrain the answer to ``schema`` through llguidance, or fail with the contract's unsupported error."""
    setter = getattr(params, "set_guidance", None)
    if setter is None:
        msg = "this GenAI has no set_guidance"
        raise GenaiError(msg, kind="json_schema_unsupported")
    try:
        setter("json_schema", schema)
    except RuntimeError as exc:
        raise GenaiError(str(exc), kind="json_schema_unsupported") from exc


def next_token(generator: Generator) -> int:
    """Decode one token; a GenAI failure mid-answer is the contract's ``generation_failed``."""
    try:
        generator.generate_next_token()
        return int(generator.get_next_tokens()[0])
    except RuntimeError as exc:
        raise GenaiError(str(exc), kind="generation_failed") from exc
