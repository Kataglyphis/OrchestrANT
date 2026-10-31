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


@pytest.mark.parametrize(
    ("provider", "operator", "auto_provider", "expected_status"),
    [
        ("DmlExecutionProvider", "DmlFusedGraph", None, 0),
        ("CPUExecutionProvider", "Conv", None, 1),
        ("DmlExecutionProvider", "MemcpyFromHost", None, 1),
        ("DmlExecutionProvider", None, None, 1),
        ("DmlExecutionProvider", "Conv", "CPUExecutionProvider", 1),
    ],
)
def test_profile_requires_directml_operator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    operator: str | None,
    auto_provider: str | None,
    expected_status: int,
) -> None:
    """An active provider alone cannot stand in for an operator placed on it."""
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            [{"cat": "Node", "args": {"provider": provider, "op_name": operator}}]
        )
    )
    sessions = []

    class Session:
        def __init__(self, *_args: object, **kwargs: Any) -> None:  # noqa: ANN401 -- ORT options are runtime-provided
            options = kwargs["sess_options"]
            assert options.enable_mem_pattern is False
            assert options.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL
            assert options.enable_profiling is True
            assert kwargs["enable_fallback"] is False
            sessions.append(kwargs["providers"])

        def disable_fallback(self) -> None:
            pass

        def get_providers(self) -> list[str]:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]

        def get_inputs(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="input", shape=[1, 3, 8, 8])]

        def run(self, *_args: object) -> None:
            pass

        def end_profiling(self) -> str:
            if len(sessions) == 2 and auto_provider:
                profile.write_text(
                    json.dumps(
                        [
                            {
                                "cat": "Node",
                                "args": {"provider": auto_provider, "op_name": "Conv"},
                            }
                        ]
                    )
                )
            return str(profile)

    monkeypatch.setattr(directml, "physical_adapter", lambda _vendor: (2, "AMD"))
    monkeypatch.setattr(
        ort, "get_available_providers", lambda: ["DmlExecutionProvider"]
    )
    monkeypatch.setattr(ort, "InferenceSession", Session)
    monkeypatch.setattr(sys, "argv", ["directml", "model.onnx"])
    if expected_status == 0:
        assert directml.main() == 0
        assert len(sessions) == 2
    else:
        with pytest.raises(RuntimeError, match="No profiled model operator"):
            directml.main()
    for providers in sessions:
        assert providers[0] == ("DmlExecutionProvider", {"device_id": 2})


def test_adapter_selection_respects_vendor_and_excludes_software() -> None:
    """Only the specified physical vendor can satisfy the proof."""
    assert directml.matches_adapter(0x1002, 0, "amd")
    assert not directml.matches_adapter(0x10DE, 0, "amd")
    assert directml.matches_adapter(0x10DE, 0, "nvidia")
    assert not directml.matches_adapter(0x10DE, 2, "nvidia")
    assert not directml.matches_adapter(0x8086, 0, None)
