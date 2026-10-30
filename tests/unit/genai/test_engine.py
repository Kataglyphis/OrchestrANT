"""The engine on the hub's tiny GenAI model, whose every answer is known, and its pure helpers."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

import pytest

from orchestrant.genai.engine import (
    GenaiEngine,
    GenaiError,
    GenerationOptions,
    check_request,
    decode_rate,
    end_tokens,
)


if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.genai.conftest import TinyModel

NEAR_THE_END = "¿"
TOKENS_TO_THE_END = 65


def user(text: str) -> list[dict[str, str]]:
    """Return one user message."""
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize(
    ("messages", "options", "problem"),
    [
        ([], GenerationOptions(), "no messages"),
        (user("a"), GenerationOptions(max_new_tokens=0), "max_new_tokens"),
        ([{"role": "robot", "content": "a"}], GenerationOptions(), "role"),
        ([{"role": "user", "content": None}], GenerationOptions(), "text content"),
    ],
)
def test_requests_no_model_can_answer_are_refused(
    messages: list, options: GenerationOptions, problem: str
) -> None:
    """Each invalid request names its problem under ``invalid_options``."""
    with pytest.raises(GenaiError, match=problem) as caught:
        check_request(messages, options)
    assert caught.value.kind == "invalid_options"


def test_end_tokens_accept_one_id_or_a_list(tmp_path: Path) -> None:
    """``genai_config.json`` spells the end token either way."""
    (tmp_path / "genai_config.json").write_text(
        json.dumps({"model": {"eos_token_id": 2}}), encoding="utf-8"
    )
    assert end_tokens(tmp_path) == {2}
    (tmp_path / "genai_config.json").write_text(
        json.dumps({"model": {"eos_token_id": [2, 7]}}), encoding="utf-8"
    )
    assert end_tokens(tmp_path) == {2, 7}


def test_the_decode_rate_counts_the_tokens_after_the_first() -> None:
    """One token or no time gives no rate."""
    assert decode_rate(1, 1.0) == 0.0
    assert decode_rate(5, 2.0) == 2.0
    assert decode_rate(5, 0.0) == 0.0


def test_a_directory_without_a_genai_config_fails_to_load(tmp_path: Path) -> None:
    """The check runs before GenAI is imported, so it holds on every host."""
    with pytest.raises(GenaiError) as caught:
        GenaiEngine(tmp_path)
    assert caught.value.kind == "model_load_failed"


def test_an_unknown_provider_is_refused(tmp_path: Path) -> None:
    """Only cpu, cuda and dml exist."""
    with pytest.raises(GenaiError) as caught:
        GenaiEngine(tmp_path, provider="tpu")
    assert caught.value.kind == "provider_unavailable"


def test_greedy_decoding_yields_the_oracles_continuation(tiny_model: TinyModel) -> None:
    """The tokens, the text and every streamed piece match ``fixture.json``."""
    pieces: list[str] = []
    engine = GenaiEngine(tiny_model.path)
    result = engine.generate(
        user(tiny_model.prompt),
        GenerationOptions(max_new_tokens=len(tiny_model.ids)),
        pieces.append,
    )
    assert result.tokens == tiny_model.ids
    assert result.text == tiny_model.continuation == "".join(pieces)
    assert result.prompt_tokens == len(tiny_model.prompt)
    assert result.finish_reason == "length"
    assert result.decode_tokens_per_second > 0


class SlowTokenizer:
    """The real tokenizer, with encoding as slow as a long prompt's."""

    def __init__(self, inner: object) -> None:
        """Wrap ``inner``."""
        self._inner = inner

    def encode(self, text: str) -> object:
        """Encode after 0.2 s."""
        time.sleep(0.2)
        return self._inner.encode(text)

    def __getattr__(self, name: str) -> object:
        """Pass everything else through."""
        return getattr(self._inner, name)


def test_the_first_tokens_time_includes_the_prefill(tiny_model: TinyModel) -> None:
    """A slow prompt step counts towards the time to the first token."""
    engine = GenaiEngine(tiny_model.path)
    engine._tokenizer = SlowTokenizer(engine._tokenizer)  # noqa: SLF001 - the seam before the prefill
    result = engine.generate(
        user(tiny_model.prompt), GenerationOptions(max_new_tokens=1)
    )
    assert result.time_to_first_token_ms >= 200


def test_the_end_token_stops_before_the_budget(tiny_model: TinyModel) -> None:
    """From 0xBF the model counts up to its end token, 256."""
    result = GenaiEngine(tiny_model.path).generate(
        user(NEAR_THE_END), GenerationOptions(max_new_tokens=TOKENS_TO_THE_END + 10)
    )
    assert len(result.tokens) == TOKENS_TO_THE_END
    assert (result.tokens[0], result.tokens[-1]) == (0xC0, 0x100)
    assert result.finish_reason == "stop"


def test_a_cancel_from_another_thread_ends_the_run_after_its_token(
    tiny_model: TinyModel,
) -> None:
    """The callback holds the first piece until the cancel has been sent."""
    engine = GenaiEngine(tiny_model.path)
    first, released = threading.Event(), threading.Event()

    def hold(_piece: str) -> None:
        first.set()
        released.wait(timeout=10)

    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            engine.generate(
                user(tiny_model.prompt),
                GenerationOptions(max_new_tokens=len(tiny_model.ids)),
                hold,
            )
        )
    )
    worker.start()
    assert first.wait(timeout=10)
    engine.cancel()
    released.set()
    worker.join(timeout=30)
    assert results[0].finish_reason == "cancelled"
    assert len(results[0].tokens) == 1


def test_a_cancel_left_from_an_earlier_run_does_not_stop_the_next(
    tiny_model: TinyModel,
) -> None:
    """Each run clears the flag as it starts."""
    engine = GenaiEngine(tiny_model.path)
    engine.cancel()
    result = engine.generate(
        user(tiny_model.prompt), GenerationOptions(max_new_tokens=len(tiny_model.ids))
    )
    assert result.text == tiny_model.continuation


def test_concurrent_callers_take_turns(tiny_model: TinyModel) -> None:
    """Four threads on one engine each get the whole answer."""
    engine = GenaiEngine(tiny_model.path)
    options = GenerationOptions(max_new_tokens=len(tiny_model.ids))
    texts: list[str] = []
    threads = [
        threading.Thread(
            target=lambda: texts.append(
                engine.generate(user(tiny_model.prompt), options).text
            )
        )
        for _ in range(4)
    ]
    deadline = time.monotonic() + 60
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    assert texts == [tiny_model.continuation] * 4
