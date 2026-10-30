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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from orchestrant.genai.engine import (
    GenerationOptions,
    GenerationResult,
    check_request,
)
from orchestrant.genai.server import Server


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from orchestrant.genai.engine import GenaiError

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
    failure: GenaiError | None = None
    asked: list[tuple[list[dict[str, Any]], GenerationOptions]] = field(
        default_factory=list
    )
    cancelled: threading.Event = field(default_factory=threading.Event)

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        """Return the pieces as one result, streaming each to ``on_text``."""
        options = options or GenerationOptions()
        check_request(messages, options)
        self.asked.append((list(messages), options))
        if self.failure:
            raise self.failure
        result = GenerationResult(
            prompt_tokens=7, finish_reason="stop", time_to_first_token_ms=1.5
        )
        for piece in self.pieces:
            if self.cancelled.is_set():
                result.finish_reason = "cancelled"
                break
            result.text += piece
            result.tokens.append(len(result.tokens))
            if on_text:
                on_text(piece)
        return result

    def cancel(self) -> None:
        """Record the cancel."""
        self.cancelled.set()


@pytest.fixture
def fake_engine() -> type[FakeEngine]:
    """Hand the suites the fake engine's class; a test module importing it finds benchmarks/tests first."""
    return FakeEngine


@pytest.fixture
def serve() -> Iterator[Callable[[Any], str]]:
    """Start a server on a free port for an engine and return its base URL; every one stops after the test."""
    servers: list[tuple[Server, threading.Thread]] = []

    def start(engine: Any) -> str:  # noqa: ANN401 - a real engine or a fake
        server = Server(("127.0.0.1", 0), engine, "tiny")
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
