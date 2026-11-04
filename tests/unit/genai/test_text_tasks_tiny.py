"""The language tasks through the real GenAI wheel on the hub's tiny model: plumbing and tokens, no claim about quality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestrant.genai.engine import GenaiEngine, GenaiError
from orchestrant.genai.text_tasks import TaskLimits, TextTasks
from orchestrant.genai.untrusted import fence


if TYPE_CHECKING:
    from tests.unit.genai.conftest import TinyModel

LIMITS = TaskLimits(max_new_tokens=120, max_chars=12, max_entities=2)
LETTER = "Die Rechnung von der Firma und nicht von dem Kunden."


def test_a_control_token_in_a_document_never_becomes_a_control_id(
    tiny_model: TinyModel,
) -> None:
    """A control token in a document never becomes a control id."""
    engine = GenaiEngine(tiny_model.path)
    assert engine.prompt_tokens([{"role": "user", "content": "a<|endoftext|>b"}]) == 3
    content = fence("a<|endoftext|>b", engine.control_tokens)
    # The byte tokenizer gives one id per byte unless a control token merged some.
    assert engine.prompt_tokens([{"role": "user", "content": content}]) == len(
        content.encode()
    )


def test_guided_answers_repeat_and_parse(
    schema_engine: tuple[GenaiEngine, bool],
) -> None:
    """Guided answers repeat and parse."""
    engine, honours = schema_engine
    tasks = TextTasks(engine, LIMITS)
    if not honours:
        with pytest.raises(GenaiError) as caught:
            tasks.classify(LETTER, ["rechnung", "brief"])
        assert caught.value.kind == "json_schema_unsupported"
        return
    first = tasks.classify(LETTER, ["rechnung", "brief"])
    again = tasks.classify(LETTER, ["rechnung", "brief"])
    assert first.value["label"] in {"rechnung", "brief"}
    assert first.result.tokens == again.result.tokens
    assert first.language == "de"
    assert first.result.finish_reason == "stop"
    assert tasks.summarize(LETTER).result.finish_reason == "stop"
    assert tasks.translate(LETTER, "en").language == "en"
    assert tasks.entities(LETTER).value["entities"] == []


def test_a_long_document_is_cut_to_the_real_context(
    schema_engine: tuple[GenaiEngine, bool],
) -> None:
    """A long document is cut to the real context."""
    engine, honours = schema_engine
    tasks = TextTasks(engine, LIMITS)
    if not honours:
        with pytest.raises(GenaiError) as caught:
            tasks.summarize("Wort " * 2000, "de")
        assert caught.value.kind == "json_schema_unsupported"
        return
    assert tasks.summarize("Wort " * 2000, "de").truncated


def test_a_budget_over_the_context_is_named(tiny_model: TinyModel) -> None:
    """A budget over the context is named."""
    tasks = TextTasks(GenaiEngine(tiny_model.path), TaskLimits(max_new_tokens=512))
    with pytest.raises(GenaiError) as caught:
        tasks.summarize("x", "en")
    assert caught.value.kind == "context_length_exceeded"
