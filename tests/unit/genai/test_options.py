"""Seed, JSON schema and stop strings reach GenAI, checked on a fake module on every leg; the mutation lane runs this file."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from orchestrant.genai.engine import (
    GenaiError,
    GenerationOptions,
    StopScan,
    check_request,
)
from orchestrant.genai.server import options_from


if TYPE_CHECKING:
    from collections.abc import Callable

    from orchestrant.genai.engine import GenaiEngine

SCHEMA = json.dumps({"type": "object", "properties": {"a": {"type": "string"}}})


def user(text: str) -> list[dict[str, str]]:
    """Return one user message."""
    return [{"role": "user", "content": text}]


def test_a_seed_reaches_genais_search_options(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """``random_seed`` carries the seed; without one GenAI picks its own."""
    engine, og = fake_genai()
    engine.generate(user("abc"), GenerationOptions(temperature=1.0, seed=7))
    engine.generate(user("abc"), GenerationOptions(temperature=1.0))
    assert og.searches[0]["random_seed"] == 7
    assert "random_seed" not in og.searches[1]


def test_temperature_zero_decodes_greedily_and_above_it_samples(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """Greedy decoding is what makes the contract tests' token ids repeatable."""
    engine, og = fake_genai()
    engine.generate(user("abc"), GenerationOptions(max_new_tokens=4))
    engine.generate(user("abc"), GenerationOptions(max_new_tokens=4, temperature=0.7))
    assert og.searches[0] == {"max_length": 7, "do_sample": False}
    assert og.searches[1]["do_sample"] is True
    assert og.searches[1]["temperature"] == 0.7


def test_a_json_schema_is_handed_to_llguidance(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """The schema text goes to ``set_guidance`` unchanged."""
    engine, og = fake_genai()
    engine.generate(user("abc"), GenerationOptions(json_schema=SCHEMA))
    assert og.guided == [("json_schema", SCHEMA)]


def test_a_genai_without_llguidance_refuses_a_schema_by_name(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """The Windows GenAI builds without llguidance; its refusal is the contract's own error, never an unguided answer."""
    engine, og = fake_genai(guidance=False)
    with pytest.raises(GenaiError) as caught:
        engine.generate(user("abc"), GenerationOptions(json_schema=SCHEMA))
    assert caught.value.kind == "json_schema_unsupported"
    og.GeneratorParams = lambda _model: SimpleNamespace(set_search_options=dict)
    with pytest.raises(GenaiError) as missing:
        engine.generate(user("abc"), GenerationOptions(json_schema=SCHEMA))
    assert missing.value.kind == "json_schema_unsupported"


def test_a_stop_string_ends_the_answer_before_it(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """``abc`` continues ``defg``; a stop at ``f`` leaves ``de`` and finish reason stop, and no piece carries the ``f``."""
    engine, _ = fake_genai()
    pieces: list[str] = []
    result = engine.generate(
        user("abc"),
        GenerationOptions(max_new_tokens=4, stop=("fg", "x")),
        pieces.append,
    )
    assert (result.text, result.finish_reason) == ("de", "stop")
    assert "".join(pieces) == "de"


def test_text_held_for_a_stop_string_that_never_comes_is_still_answered(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """``g`` could start ``gz``, so it waits; the budget ends the run and it is sent."""
    engine, _ = fake_genai()
    result = engine.generate(
        user("abc"), GenerationOptions(max_new_tokens=4, stop=("gz",))
    )
    assert (result.text, result.finish_reason) == ("defg", "length")


def test_the_stop_scan_holds_only_a_possible_prefix() -> None:
    """A partial match waits; a broken one is released."""
    scan = StopScan(["END"])
    assert scan.feed("abE") == ("ab", False)
    assert scan.feed("x") == ("Ex", False)
    assert scan.feed("EN") == ("", False)
    assert scan.feed("D!") == ("", True)
    assert scan.flush() == ""


def test_a_budget_over_the_context_is_refused_before_decoding(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """The fake model's context is 512 tokens."""
    engine, og = fake_genai()
    with pytest.raises(GenaiError) as caught:
        engine.generate(user("abc"), GenerationOptions(max_new_tokens=510))
    assert caught.value.kind == "context_length_exceeded"
    assert og.searches == []


def test_a_genai_failure_mid_answer_is_generation_failed(
    fake_genai: Callable[..., tuple[GenaiEngine, SimpleNamespace]],
) -> None:
    """A RuntimeError from the native decode is named, not leaked."""
    engine, og = fake_genai()

    def broken(_model: object, _params: object) -> object:
        class Broken:
            def append_tokens(self, _tokens: object) -> None:
                """Take the prompt."""

            def is_done(self) -> bool:
                """Never done."""
                return False

            def generate_next_token(self) -> None:
                """Fail the way a native error does."""
                msg = "native decode failed"
                raise RuntimeError(msg)

        return Broken()

    og.Generator = broken
    with pytest.raises(GenaiError) as caught:
        engine.generate(user("abc"))
    assert caught.value.kind == "generation_failed"


@pytest.mark.parametrize(
    ("options", "problem"),
    [
        (GenerationOptions(seed=-1), "seed"),
        (GenerationOptions(seed=2**31), "seed"),
        (GenerationOptions(stop=("",)), "stop"),
        (GenerationOptions(json_schema="{not json"), "not JSON"),
        (GenerationOptions(json_schema="[]"), "not a JSON object"),
        (GenerationOptions(temperature=float("nan")), "finite"),
    ],
)
def test_options_no_decode_can_honour_are_refused(
    options: GenerationOptions, problem: str
) -> None:
    """Each names its problem under ``invalid_options``."""
    with pytest.raises(GenaiError, match=problem) as caught:
        check_request(user("a"), options)
    assert caught.value.kind == "invalid_options"


def test_openai_seed_response_format_and_stop_map_onto_the_options() -> None:
    """The request names OpenAI uses become the contract's options."""
    schema = {"type": "object", "properties": {"city": {"type": "string"}}}
    options = options_from(
        {
            "seed": 3,
            "stop": "\n",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": schema},
            },
        }
    )
    assert options.seed == 3
    assert options.stop == ("\n",)
    assert json.loads(options.json_schema or "") == schema
    assert options_from({"stop": ["a", "b"]}).stop == ("a", "b")
    assert json.loads(
        options_from({"response_format": {"type": "json_object"}}).json_schema or ""
    ) == {"type": "object"}
    plain = options_from({"response_format": {"type": "text"}})
    assert (plain.json_schema, plain.seed, plain.stop) == (None, None, ())


@pytest.mark.parametrize(
    "body",
    [
        {"seed": "3"},
        {"seed": True},
        {"stop": 5},
        {"stop": ["a", 1]},
        {"response_format": "json"},
        {"response_format": {"type": "json_schema"}},
        {"response_format": {"type": "grammar"}},
        {"max_tokens": float("inf")},
    ],
)
def test_malformed_openai_options_are_invalid(body: dict[str, object]) -> None:
    """Each is refused before a model sees it."""
    with pytest.raises(GenaiError) as caught:
        options_from(body)
    assert caught.value.kind == "invalid_options"
