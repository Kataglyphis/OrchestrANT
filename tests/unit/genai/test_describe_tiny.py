"""One scene description through the real GenAI wheel on the hub's tiny model: every token follows its rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrant.genai.engine import GenaiEngine
from orchestrant.yolo.describe import describe_scene, scene_messages


if TYPE_CHECKING:
    from tests.unit.genai.conftest import TinyModel


def test_the_tokens_are_the_tiny_models_successors(tiny_model: TinyModel) -> None:
    """The tokens are the tiny models successors."""
    engine = GenaiEngine(tiny_model.path)
    counts = {"cat": 2}
    found = describe_scene(engine, counts, "en", 6)
    # The tiny template concatenates the messages, and each byte predicts its successor.
    last = "".join(m["content"] for m in scene_messages(counts, "en", ())).encode()[-1]
    assert found.tokens == list(range(last + 1, last + 7))
    assert found.finish_reason == "length"
    assert found.counts == counts
