"""Unit tests for yolo-monitor's argument defaults and execution-provider choice."""

from __future__ import annotations

import pytest

from orchestrant import paths
from orchestrant.yolo import monitor
from orchestrant.yolo.cli import parse_args


def test_model_defaults_to_the_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """No --model means the searched default, not a working-directory literal."""
    monkeypatch.setattr(
        "orchestrant.yolo.cli.default_model_path", lambda: paths.Path("x/model.onnx")
    )
    args = parse_args([])
    assert args.model == str(paths.Path("x/model.onnx"))
    assert args.self_test is False


def test_explicit_model_is_kept() -> None:
    """An explicit --model is never replaced."""
    assert parse_args(["--model", "mine.onnx", "--self-test"]).model == "mine.onnx"


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (["CPUExecutionProvider"], ["CPUExecutionProvider"]),
        (
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            ["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
        (
            ["CPUExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"],
            ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"],
        ),
    ],
)
def test_providers_follow_the_build(
    monkeypatch: pytest.MonkeyPatch, available: list[str], expected: list[str]
) -> None:
    """Only providers this ORT build has, CUDA before DirectML before CPU."""
    monkeypatch.setattr(monitor.ort, "get_available_providers", lambda: available)
    names = [p[0] if isinstance(p, tuple) else p for p in monitor.select_providers(0)]
    assert names == expected
