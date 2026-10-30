"""``orchestrant-chat`` on a fake engine and on the tiny GenAI model."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from orchestrant.genai import chat


if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.unit.genai.conftest import FakeEngine, TinyModel


def test_one_prompt_streams_its_answer_and_the_figures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_engine: type[FakeEngine],
) -> None:
    """The answer goes to stdout, the figures to stderr."""
    engine = fake_engine()
    monkeypatch.setattr(chat, "GenaiEngine", lambda *_args: engine)
    out = io.StringIO()
    assert (
        chat.main(
            ["--model-dir", "m", "--prompt", "hi", "--system", "Be brief."], stdout=out
        )
        == 0
    )
    assert out.getvalue() == "The capital is Paris.\n"
    assert "3 tokens, 2 ms to the first" in capsys.readouterr().err
    assert engine.asked[0][0] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "hi"},
    ]


def test_stdin_lines_are_one_conversation(
    monkeypatch: pytest.MonkeyPatch, fake_engine: type[FakeEngine]
) -> None:
    """The second turn sees the first answer; a blank line is skipped."""
    engine = fake_engine()
    monkeypatch.setattr(chat, "GenaiEngine", lambda *_args: engine)
    assert (
        chat.main(
            ["--model-dir", "m"],
            stdin=io.StringIO("first\n\nsecond\n"),
            stdout=io.StringIO(),
        )
        == 0
    )
    assert [m["role"] for m in engine.asked[1][0]] == ["user", "assistant", "user"]


def test_a_model_that_does_not_load_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The contract's error, not a traceback."""
    assert (
        chat.main(
            ["--model-dir", str(tmp_path), "--prompt", "hi"], stdout=io.StringIO()
        )
        == 2
    )
    assert "model_load_failed" in capsys.readouterr().err


def test_the_tiny_model_answers_with_its_oracle(tiny_model: TinyModel) -> None:
    """The real engine behind the command line."""
    out = io.StringIO()
    argv = [
        "--model-dir",
        str(tiny_model.path),
        "--prompt",
        tiny_model.prompt,
        "--max-new-tokens",
        str(len(tiny_model.ids)),
    ]
    assert chat.main(argv, stdout=out) == 0
    assert out.getvalue() == tiny_model.continuation + "\n"
