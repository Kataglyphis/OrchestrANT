"""The genai suites' fixtures: the hub's tiny GenAI model and a fake engine for the server."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import sysconfig
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from orchestrant.genai.engine import (
    GenaiEngine,
    GenerationOptions,
    GenerationResult,
    check_request,
)
from orchestrant.genai.server import TURN_WAIT, Server


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from orchestrant.genai.server import Engine

GENERATOR = (
    Path(__file__).resolve().parents[3]
    / "third_party/ANTfrastructure/linux/scripts/lib/tiny-genai-model.py"
)


@dataclass
class TinyModel:
    """The model directory and its ``fixture.json`` oracle."""

    path: Path
    prompt: str
    continuation: str
    ids: list[int]


def genai_required() -> bool:
    """Return whether this leg must run the GenAI cases: CI asks, and only GIL legs off riscv64 install the wheel."""
    free_threaded = sysconfig.get_config_var("Py_GIL_DISABLED") == 1
    asked = os.environ.get("ORCHESTRANT_REQUIRE_GENAI") == "1"
    return asked and not free_threaded and platform.machine() != "riscv64"


@pytest.fixture
def genai_lane() -> Callable[[], bool]:
    """Hand ``genai_required`` to modules that cannot import this conftest."""
    return genai_required


@pytest.fixture(scope="session")
def tiny_model(tmp_path_factory: pytest.TempPathFactory) -> TinyModel:
    """Write the tiny model once; skip without GenAI unless ``ORCHESTRANT_REQUIRE_GENAI=1``."""
    if importlib.util.find_spec("onnxruntime_genai") is None:
        if genai_required():
            pytest.fail(
                "ORCHESTRANT_REQUIRE_GENAI=1 but onnxruntime_genai is not importable"
            )
        pytest.skip(
            "no onnxruntime_genai here; the image lanes set ORCHESTRANT_REQUIRE_GENAI=1"
        )
    out = tmp_path_factory.mktemp("tiny-genai")
    subprocess.run(  # noqa: S603 - this interpreter on the hub's generator
        [sys.executable, str(GENERATOR), "--out", str(out)], check=True, timeout=120
    )
    oracle = json.loads((out / "fixture.json").read_text(encoding="utf-8"))
    return TinyModel(
        out, oracle["prompt"], oracle["continuation"], oracle["continuation_ids"]
    )


@dataclass
class FakeEngine:
    """Plays back fixed pieces, honours a cancel, and records what it was asked."""

    pieces: list[str] = field(default_factory=lambda: ["The capital", " is", " Paris."])
    failure: Exception | None = None
    delay: float = 0.0
    asked: list[tuple[list[dict[str, Any]], GenerationOptions]] = field(
        default_factory=list
    )
    results: list[GenerationResult] = field(default_factory=list)
    cancelled: threading.Event = field(default_factory=threading.Event)

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
        on_text: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Return the pieces as one result, streaming each to ``on_text``; an engine-wide cancel is never cleared."""
        options = options or GenerationOptions()
        check_request(messages, options)
        self.asked.append((list(messages), options))
        if self.failure:
            raise self.failure
        result = GenerationResult(
            prompt_tokens=7, finish_reason="stop", time_to_first_token_ms=1.5
        )
        for piece in self.pieces:
            if self.cancelled.is_set() or (cancel is not None and cancel.is_set()):
                result.finish_reason = "cancelled"
                break
            time.sleep(self.delay)
            result.text += piece
            result.tokens.append(len(result.tokens))
            if on_text:
                on_text(piece)
        self.results.append(result)
        return result

    def cancel(self) -> None:
        """Record the cancel."""
        self.cancelled.set()


@pytest.fixture
def fake_engine() -> type[FakeEngine]:
    """Hand the suites the fake engine's class; a test module importing it finds benchmarks/tests first."""
    return FakeEngine


class FakeParams:
    """GeneratorParams that record their search options and guidance into the owning fake module."""

    def __init__(self, owner: SimpleNamespace) -> None:
        """Record into ``owner``."""
        self._owner = owner
        self.search: dict[str, object] = {}

    def set_search_options(self, **options: object) -> None:
        """Record the options."""
        self.search = options
        self._owner.searches.append(options)

    def set_guidance(self, kind: str, data: str) -> None:
        """Record the guidance, or refuse it the way a GenAI built without llguidance does."""
        if not self._owner.guidance:
            msg = "Guidance is not enabled in this build of onnxruntime-genai"
            raise RuntimeError(msg)
        self._owner.guided.append((kind, data))


class FakeGenerator:
    """Every byte predicts its successor and 255 the end token 256, the tiny model's rule."""

    def __init__(self, params: FakeParams) -> None:
        """Decode with ``params``' budget."""
        self._budget = cast("int", params.search["max_length"])
        self._tokens: list[int] = []

    def append_tokens(self, tokens: Sequence[int]) -> None:
        """Take the prompt."""
        self._tokens += list(tokens)

    def is_done(self) -> bool:
        """Stop at the end token or the budget."""
        return len(self._tokens) >= self._budget or self._tokens[-1] == END_TOKEN

    def generate_next_token(self) -> None:
        """Append the successor."""
        self._tokens.append(min(self._tokens[-1] + 1, END_TOKEN))

    def get_next_tokens(self) -> list[int]:
        """Return the newest token."""
        return [self._tokens[-1]]


class FakeTokenizer:
    """Bytes are tokens; the chat template is the last message's text."""

    def __init__(self, _model: object) -> None:
        """Ignore the model."""

    def encode(self, text: str) -> list[int]:
        """Return the UTF-8 bytes."""
        return list(text.encode())

    def apply_chat_template(self, chat: str, *, add_generation_prompt: bool) -> str:
        """Return the last message's content."""
        assert add_generation_prompt
        return str(json.loads(chat)[-1]["content"])

    def create_stream(self) -> FakeTokenizer:
        """Decode token by token."""
        return self

    def decode(self, token: int) -> str:
        """Return a byte token's character, nothing for the end token."""
        return chr(token) if token < END_TOKEN else ""


END_TOKEN = 256


class FakeConfig:
    """A config that takes any provider."""

    def __init__(self, _path: str) -> None:
        """Ignore the path."""

    def clear_providers(self) -> None:
        """Take no provider."""

    def append_provider(self, _name: str) -> None:
        """Take any provider."""


def fake_genai_module(*, guidance: bool = True) -> SimpleNamespace:
    """Stand in for the ``onnxruntime_genai`` module, so option forwarding is tested on every leg."""
    og = SimpleNamespace(guidance=guidance, searches=[], guided=[])
    og.Config = FakeConfig
    og.Model = lambda config: config
    og.Tokenizer = FakeTokenizer
    og.GeneratorParams = lambda _model: FakeParams(og)
    og.Generator = lambda _model, params: FakeGenerator(params)
    return og


@pytest.fixture(name="fake_genai_module")
def fake_genai_module_fixture() -> Callable[..., SimpleNamespace]:
    """Hand the fake module's factory to suites that cannot import this conftest."""
    return fake_genai_module


def fake_model_dir(path: Path) -> Path:
    """Write the ``genai_config.json`` the engine reads before it loads anything."""
    config = {"model": {"eos_token_id": END_TOKEN, "context_length": 512}}
    (path / "genai_config.json").write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture
def fake_genai(tmp_path: Path) -> Callable[..., tuple[GenaiEngine, SimpleNamespace]]:
    """Return a factory for an engine on the fake module, with or without guidance."""

    def build(*, guidance: bool = True) -> tuple[GenaiEngine, SimpleNamespace]:
        og = fake_genai_module(guidance=guidance)
        return GenaiEngine(fake_model_dir(tmp_path), og=cast("Any", og)), og

    return build


@pytest.fixture
def schema_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[GenaiEngine, bool]:
    """Return an engine and whether it must honour a schema: GenAI with llguidance on the image legs, else a fake without it."""
    if genai_required():
        tiny: TinyModel = request.getfixturevalue("tiny_model")
        return GenaiEngine(tiny.path), True
    og = fake_genai_module(guidance=False)
    return GenaiEngine(fake_model_dir(tmp_path), og=cast("Any", og)), False


@pytest.fixture
def serve() -> Iterator[Callable[..., str]]:
    """Start a server on a free port for an engine and return its base URL; every one stops after the test."""
    servers: list[tuple[Server, threading.Thread]] = []

    def start(
        engine: Engine,
        *,
        cors_origin: str | None = None,
        api_key: str | None = None,
        turn_wait: float = TURN_WAIT,
    ) -> str:
        server = Server(
            ("127.0.0.1", 0),
            engine,
            "tiny",
            cors_origin=cors_origin,
            api_key=api_key,
            turn_wait=turn_wait,
        )
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
