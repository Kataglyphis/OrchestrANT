"""The Windows hardware proof must reject a CPU-only model profile."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from orchestrant.smoke import directml


ort = pytest.importorskip("onnxruntime")


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("provider", ["DmlExecutionProvider", "CPUExecutionProvider"])
def test_profile_requires_directml_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    """An active provider alone cannot stand in for an operator placed on it."""
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps([{"cat": "Node", "args": {"provider": provider}}]))

    class Session:
        def __init__(self, *_args: object, **kwargs: Any) -> None:  # noqa: ANN401 -- ORT options are runtime-provided
            options = kwargs["sess_options"]
            assert options.enable_mem_pattern is False
            assert options.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL

        def disable_fallback(self) -> None:
            pass

        def get_providers(self) -> list[str]:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]

        def get_inputs(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="input", shape=[1, 3, 8, 8])]

        def run(self, *_args: object) -> None:
            pass

        def end_profiling(self) -> str:
            return str(profile)

    monkeypatch.setattr(directml, "physical_adapter", lambda _vendor: (0, "AMD"))
    monkeypatch.setattr(
        ort, "get_available_providers", lambda: ["DmlExecutionProvider"]
    )
    monkeypatch.setattr(ort, "InferenceSession", Session)
    monkeypatch.setattr(sys, "argv", ["directml", "model.onnx"])
    if provider == "DmlExecutionProvider":
        assert directml.main() == 0
    else:
        with pytest.raises(RuntimeError, match="No profiled model operator"):
            directml.main()


def test_adapter_selection_respects_vendor_and_excludes_software() -> None:
    """Only the specified physical vendor can satisfy the proof."""
    assert directml.matches_adapter(0x1002, 0, "amd")
    assert not directml.matches_adapter(0x10DE, 0, "amd")
    assert directml.matches_adapter(0x10DE, 0, "nvidia")
    assert not directml.matches_adapter(0x10DE, 2, "nvidia")
    assert not directml.matches_adapter(0x8086, 0, None)
