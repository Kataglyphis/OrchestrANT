"""GenaiEngine's decode loop, provider choice and error kinds over a fake onnxruntime_genai module, on every host."""

from __future__ import annotations

import functools
import json
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from orchestrant.genai.engine import GenaiEngine, GenaiError, GenerationOptions


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

EOS = 9
PROMPT = [10, 11, 12]
PIECES = {1: "The", 2: " capital", 3: " is", 4: " Paris", 5: ".", EOS: ""}
ANSWER = [1, 2, 3, 4, 5, EOS]


@dataclass
class Script:
    """What the fake library decodes and fails with, and what the engine asked of it."""

    tokens: list[int] = field(default_factory=lambda: list(ANSWER))
    load_error: str = ""
    template_error: str = ""
    generator_error: str = ""
    prefill_seconds: float = 0.0
    providers: list[str] = field(default_factory=list)
    cleared: bool = False
    search: dict[str, Any] = field(default_factory=dict)
    chats: list[str] = field(default_factory=list)


class Config:
    """``og.Config``: records the provider list the engine leaves."""

    def __init__(self, script: Script, _path: str) -> None:
        """Start with the config file's own provider, as GenAI does."""
        self.script = script
        script.providers.append("default")

    def clear_providers(self) -> None:
        """Drop every provider."""
        self.script.providers.clear()
        self.script.cleared = True

    def append_provider(self, name: str) -> None:
        """Add one provider."""
        self.script.providers.append(name)


class Model:
    """``og.Model``: fails to load when the script says so."""

    def __init__(self, config: Config) -> None:
        """Load, or raise the scripted load error."""
        self.script = config.script
        if self.script.load_error:
            raise RuntimeError(self.script.load_error)


class Stream:
    """``Tokenizer.create_stream()``: one piece per token."""

    def decode(self, token: int) -> str:
        """Return the token's piece."""
        return PIECES[token]


class Tokenizer:
    """``og.Tokenizer``: a fixed prompt, and a template that records its chat."""

    def __init__(self, model: Model) -> None:
        """Share the model's script."""
        self.script = model.script

    def encode(self, _text: str) -> list[int]:
        """Return the fixed prompt ids."""
        return list(PROMPT)

    def apply_chat_template(self, chat: str, *, add_generation_prompt: bool) -> str:
        """Record the chat, or raise the scripted template error."""
        if self.script.template_error:
            raise RuntimeError(self.script.template_error)
        self.script.chats.append(chat)
        return f"<prompt add={add_generation_prompt}>"

    def create_stream(self) -> Stream:
        """Return a fresh stream."""
        return Stream()


class GeneratorParams:
    """``og.GeneratorParams``: records the search options."""

    def __init__(self, model: Model) -> None:
        """Share the model's script."""
        self.script = model.script

    def set_search_options(self, **options: object) -> None:
        """Record the options."""
        self.script.search = dict(options)


class Generator:
    """``og.Generator``: decodes the scripted tokens until the end token, the budget or the script runs out."""

    def __init__(self, model: Model, _params: GeneratorParams) -> None:
        """Build, or raise the scripted generator error."""
        self.script = model.script
        if self.script.generator_error:
            raise RuntimeError(self.script.generator_error)
        self.left = list(self.script.tokens)
        self.length = 0
        self.last = -1

    def append_tokens(self, prompt: Sequence[int]) -> None:
        """Take the prompt after the scripted prefill time."""
        time.sleep(self.script.prefill_seconds)
        self.length = len(prompt)

    def is_done(self) -> bool:
        """Return whether the end token, the budget or the script ended it."""
        budget = int(self.script.search["max_length"])
        return not self.left or self.last == EOS or self.length >= budget

    def generate_next_token(self) -> None:
        """Decode the next scripted token."""
        self.last = self.left.pop(0)
        self.length += 1

    def get_next_tokens(self) -> list[int]:
        """Return the token just decoded."""
        return [self.last]


def fake_genai(script: Script) -> types.ModuleType:
    """Return a module with the names the engine calls on ``onnxruntime_genai``, driven by ``script``."""
    module = types.ModuleType("onnxruntime_genai")
    vars(module).update(
        Config=functools.partial(Config, script),
        Model=Model,
        Tokenizer=Tokenizer,
        GeneratorParams=GeneratorParams,
        Generator=Generator,
    )
    return module


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """Write the one file the engine reads itself, naming the end token."""
    (tmp_path / "genai_config.json").write_text(
        json.dumps({"model": {"eos_token_id": [EOS]}}), encoding="utf-8"
    )
    return tmp_path


def user(text: str) -> list[dict[str, str]]:
    """Return one user message."""
    return [{"role": "user", "content": text}]


def test_greedy_decoding_streams_each_piece_and_stops_at_the_end_token(
    model_dir: Path,
) -> None:
    """The text, ids, prompt size and search options of a greedy run up to the end token."""
    script = Script()
    pieces: list[str] = []
    engine = GenaiEngine(model_dir, og=fake_genai(script))
    result = engine.generate(
        user("Capital?"), GenerationOptions(max_new_tokens=10), pieces.append
    )
    assert result.text == "The capital is Paris."
    assert pieces == ["The", " capital", " is", " Paris", "."]
    assert result.tokens == ANSWER
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == len(PROMPT)
    assert script.search == {"max_length": len(PROMPT) + 10, "do_sample": False}


def test_the_budget_ends_a_run_with_length(model_dir: Path) -> None:
    """Two new tokens after a three-token prompt, and no end token among them."""
    script = Script()
    engine = GenaiEngine(model_dir, og=fake_genai(script))
    result = engine.generate(user("Capital?"), GenerationOptions(max_new_tokens=2))
    assert result.tokens == [1, 2]
    assert result.finish_reason == "length"


def test_a_temperature_asks_for_sampling_with_its_options(model_dir: Path) -> None:
    """Sampling passes temperature, top_p and top_k on."""
    script = Script()
    engine = GenaiEngine(model_dir, og=fake_genai(script))
    options = GenerationOptions(max_new_tokens=4, temperature=0.7, top_p=0.9, top_k=5)
    engine.generate(user("Capital?"), options)
    assert script.search == {
        "max_length": len(PROMPT) + 4,
        "do_sample": True,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 5,
    }


@pytest.mark.parametrize(
    ("provider", "appended"), [("cpu", []), ("cuda", ["cuda"]), ("dml", ["dml"])]
)
def test_the_provider_replaces_the_configs_own_list(
    model_dir: Path, provider: str, appended: list[str]
) -> None:
    """CPU appends nothing, as GenAI's CPU path needs no provider entry."""
    script = Script()
    GenaiEngine(model_dir, provider=provider, og=fake_genai(script))
    assert script.cleared
    assert script.providers == appended


@pytest.mark.parametrize(
    ("provider", "message", "kind"),
    [
        ("cuda", "CUDA execution provider is not available", "provider_unavailable"),
        ("cuda", "model.onnx: bad weights", "model_load_failed"),
        ("cpu", "CUDA execution provider is not available", "model_load_failed"),
    ],
)
def test_a_load_failure_names_the_provider_only_when_it_is_to_blame(
    model_dir: Path, provider: str, message: str, kind: str
) -> None:
    """A load error blames the provider only when it names the one that was asked for."""
    with pytest.raises(GenaiError) as caught:
        GenaiEngine(
            model_dir, provider=provider, og=fake_genai(Script(load_error=message))
        )
    assert caught.value.kind == kind


def test_a_template_failure_is_its_own_kind(model_dir: Path) -> None:
    """The server maps it to 400, not 500."""
    engine = GenaiEngine(model_dir, og=fake_genai(Script(template_error="no template")))
    with pytest.raises(GenaiError) as caught:
        engine.generate(user("Capital?"))
    assert caught.value.kind == "chat_template_failed"


def test_a_prompt_the_generator_refuses_is_an_invalid_request(model_dir: Path) -> None:
    """GenAI refuses a prompt plus budget over the context when the generator is built."""
    engine = GenaiEngine(model_dir, og=fake_genai(Script(generator_error="too long")))
    with pytest.raises(GenaiError) as caught:
        engine.generate(user("Capital?"))
    assert caught.value.kind == "invalid_options"


def test_the_template_sees_only_role_and_content(model_dir: Path) -> None:
    """Keys a client adds, such as ``name``, never reach the chat template."""
    script = Script()
    engine = GenaiEngine(model_dir, og=fake_genai(script))
    engine.generate([{"role": "user", "content": "Capital?", "name": "jonas"}])
    assert json.loads(script.chats[0]) == user("Capital?")


def test_a_cancel_during_the_run_ends_it_after_the_current_token(
    model_dir: Path,
) -> None:
    """The first piece cancels, so the end token after it is never decoded."""
    engine = GenaiEngine(model_dir, og=fake_genai(Script(tokens=[1, EOS])))
    result = engine.generate(user("Capital?"), on_text=lambda _piece: engine.cancel())
    assert result.tokens == [1]
    assert result.finish_reason == "cancelled"


def test_a_cancel_before_the_run_does_not_stop_it(model_dir: Path) -> None:
    """A cancel that arrived with no run in flight is cleared when the next one starts."""
    engine = GenaiEngine(model_dir, og=fake_genai(Script()))
    engine.cancel()
    result = engine.generate(user("Capital?"))
    assert result.tokens == ANSWER
    assert result.finish_reason == "stop"


def test_the_first_tokens_time_counts_the_prefill_in_milliseconds(
    model_dir: Path,
) -> None:
    """A 50 ms prefill shows up in the first token's time."""
    engine = GenaiEngine(model_dir, og=fake_genai(Script(prefill_seconds=0.05)))
    result = engine.generate(user("Capital?"))
    assert result.time_to_first_token_ms >= 50
