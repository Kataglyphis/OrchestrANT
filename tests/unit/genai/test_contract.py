"""ModelANT's text-generation-v1 contract replayed on SmolLM2-135M, which ``llm.lock`` pins and ci_tests.sh fetches."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from orchestrant.genai.engine import GenaiEngine, GenerationOptions


if TYPE_CHECKING:
    from collections.abc import Callable


MODELS = Path(__file__).resolve().parents[3] / "resources/models"
CONTRACT = MODELS / "contracts/text-generation-v1.json"
# ModelANT 78d10cfa contracts/text-generation-v1.json, whose SHA-256 scripts/linux/ai-regression.lock pins.
CASE = "capital-of-france"
EXPECTED_IDS = [504, 3575, 282, 4649, 314, 7042, 30, 2]


@pytest.fixture
def case(genai_lane: Callable[[], bool]) -> dict:
    """Return the contract's single case, or skip where neither GenAI nor the fetched model is required."""
    missing = [
        p for p in (CONTRACT, MODELS / "smollm2_135m_instruct-cpu") if not p.exists()
    ]
    if importlib.util.find_spec("onnxruntime_genai") is None:
        missing.append(Path("onnxruntime_genai"))
    if missing:
        # ci_tests.sh fetched the model, so on a leg that installs GenAI its absence is a failure.
        if os.environ.get("ORCHESTRANT_REQUIRE_MODEL") == "1" and genai_lane():
            pytest.fail(f"ORCHESTRANT_REQUIRE_MODEL=1 but {missing} are absent")
        pytest.skip(f"{missing} absent; scripts/linux/ci_tests.sh fetches them")
    cases = json.loads(CONTRACT.read_text(encoding="utf-8"))["cases"]
    assert [c["name"] for c in cases] == [CASE]
    return cases[0]


def test_the_contract_holds_the_pinned_ids(case: dict) -> None:
    """A changed contract file is a new contract, not a pass."""
    assert case["expected_ids"] == EXPECTED_IDS
    assert case["must_contain"] == "Paris"


def test_greedy_decoding_replays_the_expected_ids(case: dict) -> None:
    """The exact token ids, then the text the case names."""
    engine = GenaiEngine(MODELS / case["model"], "cpu")
    result = engine.generate(
        case["messages"], GenerationOptions(max_new_tokens=case["max_new_tokens"])
    )
    assert result.tokens == EXPECTED_IDS
    assert case["must_contain"] in result.text
    assert result.finish_reason == "stop"
