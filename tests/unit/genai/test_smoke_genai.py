"""The packaged GenAI smoke check asserts decoded token ids, on a fake module everywhere and on the tiny model where GenAI is installed."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from orchestrant.smoke import checks


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import SimpleNamespace

    import pytest

    from tests.unit.genai.conftest import TinyModel


def oracle_dir(path: Path, continuation: list[int]) -> Path:
    """Write a ``fixture.json`` whose prompt ``abc`` continues with ``continuation``."""
    oracle = {
        "prompt_ids": [97, 98, 99],
        "continuation_ids": continuation,
        "max_length": 7,
    }
    (path / "fixture.json").write_text(json.dumps(oracle), encoding="utf-8")
    return path


def test_matching_token_ids_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_genai_module: Callable[..., SimpleNamespace],
) -> None:
    """The successor rule decodes ``defg``, ids 100 to 103."""
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", fake_genai_module())
    result = checks.check_onnxruntime_genai(oracle_dir(tmp_path, [100, 101, 102, 103]))
    assert result.ok, result.detail


def test_other_token_ids_fail_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_genai_module: Callable[..., SimpleNamespace],
) -> None:
    """A wheel that loads but decodes wrongly is a failure, not a warning."""
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", fake_genai_module())
    result = checks.check_onnxruntime_genai(oracle_dir(tmp_path, [100, 101, 102, 104]))
    assert not result.ok
    assert not result.optional
    assert "decoded [100, 101, 102, 103]" in result.detail


def test_without_a_model_the_decode_is_reported_unproven(
    monkeypatch: pytest.MonkeyPatch,
    fake_genai_module: Callable[..., SimpleNamespace],
) -> None:
    """Entry points alone never pass."""
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", fake_genai_module())
    monkeypatch.delenv(checks.GENAI_MODEL_ENV, raising=False)
    result = checks.check_onnxruntime_genai()
    assert (result.ok, result.optional) == (False, True)
    assert "unproven" in result.detail


def test_the_tiny_model_passes_through_the_environment_variable(
    tiny_model: TinyModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real wheel decodes the oracle named by ORCHESTRANT_SMOKE_GENAI_MODEL."""
    monkeypatch.setenv(checks.GENAI_MODEL_ENV, str(tiny_model.path))
    result = checks.check_onnxruntime_genai()
    assert result.ok, result.detail
    assert f"matched {len(tiny_model.ids)} token ids" in result.detail
