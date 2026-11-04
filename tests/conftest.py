"""Shared pytest setup for every test."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest


if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrant.genai.engine import GenerationOptions, GenerationResult


# TkAgg crashes headless Windows (0xC0000005); an env var for the matplotlib-less viewer job, here as tests/integration imports matplotlib first.
os.environ["MPLBACKEND"] = "Agg"


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

    def prompt_tokens(self, messages: Sequence[dict[str, Any]]) -> int:
        """Count one token per character of the concatenated messages."""
        return sum(len(message["content"]) for message in messages)

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        options: GenerationOptions | None = None,
    ) -> GenerationResult:
        """Return the next answer, or the last one again."""
        # The viewer job collects these tests with the package blocked, so nothing here imports it up front.
        engine = importlib.import_module("orchestrant.genai.engine")
        self.asked.append(list(messages))
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
