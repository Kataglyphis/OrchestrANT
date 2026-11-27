"""Shared pytest setup for every test."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest


if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence

    from orchestrant.genai.engine import GenerationOptions, GenerationResult


# TkAgg crashes headless Windows (0xC0000005); an env var for the matplotlib-less viewer job, here as tests/integration imports matplotlib first.
os.environ["MPLBACKEND"] = "Agg"

# The arm64 stage copies single sources beside the tests, never a package __init__.py, so this marks a checkout.
CHECKOUT_PACKAGE = Path(__file__).resolve().parents[1] / "orchestrant" / "__init__.py"


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker of the tests that read the checkout rather than the installed package."""
    config.addinivalue_line(
        "markers",
        "source_tree: reads the checkout's orchestrant/ sources, which a run against the installed wheel lacks",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """A source check has nothing to read where the tests run against the installed wheel alone."""
    if item.get_closest_marker("source_tree") and not CHECKOUT_PACKAGE.is_file():
        pytest.skip(
            "a source check: this run tests the installed wheel, with no checkout beside the tests"
        )


@dataclass
class ScriptedEngine:
    """Answers with fixed texts and records every prompt, as the language tasks see an engine."""

    answers: list[str] = field(default_factory=list)
    finish_reason: str = "stop"
    context_tokens: int | None = None
    control_tokens: tuple[str, ...] = ()
    asked: list[list[dict[str, Any]]] = field(default_factory=list)
    options: list[GenerationOptions] = field(default_factory=list)
    cancels: int = 0
    run_cancels: list[threading.Event | None] = field(default_factory=list)

    def prompt_tokens(self, messages: Sequence[dict[str, Any]]) -> int:
        """Count one token per character of the concatenated messages."""
        return sum(len(message["content"]) for message in messages)

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> GenerationResult:
        """Return the next answer, or the last one again; ``cancel`` is recorded, as the run's own stop."""
        # The viewer job collects these tests with the package blocked, so nothing here imports it up front.
        engine = importlib.import_module("orchestrant.genai.engine")
        self.asked.append(list(messages))
        self.run_cancels.append(cancel)
        self.options.append(options or engine.GenerationOptions())
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return engine.GenerationResult(
            text=text, tokens=[1, 2, 3], finish_reason=self.finish_reason
        )

    def cancel(self) -> None:
        """Count the cancel."""
        self.cancels += 1


@pytest.fixture
def scripted_engine() -> type[ScriptedEngine]:
    """Hand the suites the class; a test module importing a conftest finds benchmarks/tests first."""
    return ScriptedEngine
