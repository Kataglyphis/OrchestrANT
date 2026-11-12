"""The shipped model names its output layout, so its frames are decoded by position and never guessed."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, NoReturn

import numpy as np


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType, SimpleNamespace

    import pytest


CAT = 15


def _guessed(*_args: object, **_kwargs: object) -> NoReturn:
    message = "the guessing decoder ran"
    raise AssertionError(message)


def test_the_shipped_model_names_its_layout_and_two_cats_decode_without_a_guess(
    model: Path,
    contracts: Path,
    runtime: SimpleNamespace,
    diagnostic: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEIM-D-FINE-N says xyxy_score_class; with the guessing decoder made to raise, both cats are still found."""
    assert runtime.ort is not None
    pre = importlib.import_module("orchestrant.yolo.core.preprocess")
    post = importlib.import_module("orchestrant.yolo.core.postprocess")
    providers = importlib.import_module("orchestrant.yolo.providers")
    session = providers.create_session(str(model), 0, "cpu")
    assert post.output_layout(session) == "xyxy_score_class"
    monkeypatch.setattr(post, "_decode_guessed", _guessed)
    image = runtime.cv2.imread(
        str(contracts / "two_cats.jpg"), runtime.cv2.IMREAD_COLOR
    )
    found = diagnostic.capture(session, image, np, pre, post)[0]
    assert sum(item["class"] == CAT for item in found["detections"]) >= 2, found
