"""Unit tests for yolo-monitor's argument defaults and execution-provider choice."""

from __future__ import annotations

import pytest


# monitor imports OpenCV and ONNX Runtime, which the free-threaded 3.14t leg does not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

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


def test_the_ui_defaults_to_auto_in_a_window() -> None:
    """No flags: the UI is chosen at start, in a window, with --web's port ready."""
    args = parse_args([])
    assert (args.ui, args.web, args.host, args.port) == (
        "auto",
        False,
        "127.0.0.1",
        8550,
    )


@pytest.mark.parametrize(
    ("argv", "flet", "expected"),
    [
        ([], True, "flet"),
        ([], False, "opencv"),
        (["--web"], False, "flet"),
        (["--ui", "opencv"], True, "opencv"),
        (["--ui", "flet"], False, "flet"),
    ],
)
def test_auto_picks_flet_where_installed(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    flet: bool,  # noqa: FBT001 -- pytest hands the parametrized value over by name
    expected: str,
) -> None:
    """Auto means Flet where installed (a packaged build has none); --web always means Flet."""
    monkeypatch.setattr(monitor, "FLET_AVAILABLE", flet)
    assert monitor.resolve_ui(parse_args(argv)) == expected


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
