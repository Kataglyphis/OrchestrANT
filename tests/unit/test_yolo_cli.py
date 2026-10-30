"""Unit tests for yolo-monitor's argument defaults and execution-provider choice."""

from __future__ import annotations

import json
from typing import Any

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


def test_inference_device_defaults_to_auto_and_accepts_cpu() -> None:
    """CPU can be selected even if a GPU-enabled runtime is installed."""
    assert parse_args([]).device == "auto"
    assert parse_args(["--device", "cpu", "--gpu", "2"]).device == "cpu"


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
            ["MIGraphXExecutionProvider", "CPUExecutionProvider"],
            ["MIGraphXExecutionProvider", "CPUExecutionProvider"],
        ),
        (
            [
                "CPUExecutionProvider",
                "DmlExecutionProvider",
                "MIGraphXExecutionProvider",
                "CUDAExecutionProvider",
            ],
            [
                "CUDAExecutionProvider",
                "MIGraphXExecutionProvider",
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            ],
        ),
    ],
)
def test_providers_follow_the_build(
    monkeypatch: pytest.MonkeyPatch, available: list[str], expected: list[str]
) -> None:
    """Only providers this ORT build has, in the app's preferred order."""
    monkeypatch.setattr(monitor.ort, "get_available_providers", lambda: available)
    names = [p[0] if isinstance(p, tuple) else p for p in monitor.select_providers(0)]
    assert names == expected


def test_migraphx_receives_selected_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """The selected GPU index reaches the AMD provider."""
    monkeypatch.setattr(
        monitor.ort, "get_available_providers", lambda: ["MIGraphXExecutionProvider"]
    )
    assert monitor.select_providers(2) == [
        ("MIGraphXExecutionProvider", {"device_id": 2}),
        "CPUExecutionProvider",
    ]


@pytest.mark.parametrize(
    ("device", "provider"),
    [
        ("cuda", "CUDAExecutionProvider"),
        ("migraphx", "MIGraphXExecutionProvider"),
        ("directml", "DmlExecutionProvider"),
    ],
)
def test_explicit_gpu_requires_the_requested_provider(
    monkeypatch: pytest.MonkeyPatch, device: str, provider: str
) -> None:
    """An explicit GPU choice never accepts a runtime without its provider."""
    monkeypatch.setattr(
        monitor.ort, "get_available_providers", lambda: ["CPUExecutionProvider"]
    )
    with pytest.raises(RuntimeError, match=provider):
        monitor.select_providers(2, device)
    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: [provider, "CPUExecutionProvider"],
    )
    selected = monitor.select_providers(2, device)
    assert len(selected) == 1
    assert selected[0][0] == provider
    assert selected[0][1]["device_id"] == 2


def test_explicit_cpu_ignores_available_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit CPU request does not register an available GPU provider."""
    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    assert monitor.select_providers(0, "cpu") == ["CPUExecutionProvider"]


@pytest.mark.parametrize(
    ("device", "available", "directml"),
    [
        ("directml", ["DmlExecutionProvider", "CPUExecutionProvider"], True),
        ("auto", ["DmlExecutionProvider", "CPUExecutionProvider"], True),
        ("cpu", ["DmlExecutionProvider", "CPUExecutionProvider"], False),
        ("auto", ["CPUExecutionProvider"], False),
        ("cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"], False),
        ("auto", ["CUDAExecutionProvider", "CPUExecutionProvider"], False),
    ],
)
def test_directml_session_options(
    monkeypatch: pytest.MonkeyPatch,
    device: str,
    available: list[str],
    directml: bool,  # noqa: FBT001 -- parametrized fixture
) -> None:
    """DirectML settings apply only when the provider is registered."""
    monkeypatch.setattr(monitor.ort, "get_available_providers", lambda: available)

    class Session:
        def __init__(
            self,
            _model: str,
            *,
            sess_options: Any,  # noqa: ANN401 -- ORT options are runtime-provided
            providers: list,
            enable_fallback: bool,
        ) -> None:
            assert enable_fallback is False
            if directml:
                assert sess_options.enable_mem_pattern is False
                assert (
                    sess_options.execution_mode
                    == monitor.ort.ExecutionMode.ORT_SEQUENTIAL
                )
            else:
                assert sess_options is None
            self.providers = [p[0] if isinstance(p, tuple) else p for p in providers]

        def get_providers(self) -> list[str]:
            return self.providers

        def disable_fallback(self) -> None:
            pass

    monkeypatch.setattr(monitor.ort, "InferenceSession", Session)
    monitor.create_session("model.onnx", 0, device)


@pytest.mark.parametrize(
    ("device", "provider"),
    [
        ("cuda", "CUDAExecutionProvider"),
        ("migraphx", "MIGraphXExecutionProvider"),
        ("directml", "DmlExecutionProvider"),
    ],
)
def test_explicit_gpu_rejects_ort_constructor_fallback(
    monkeypatch: pytest.MonkeyPatch, device: str, provider: str
) -> None:
    """A provider failure during session construction cannot silently select CPU."""

    class CpuSession:
        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: [provider, "CPUExecutionProvider"],
    )
    monkeypatch.setattr(
        monitor.ort, "InferenceSession", lambda *_args, **_kwargs: CpuSession()
    )
    with pytest.raises(RuntimeError, match=f"did not activate {provider}"):
        monitor.create_session("model.onnx", 0, device)


@pytest.mark.parametrize("cuda_failure", ["cpu_fallback", "exception", "ort_failure"])
def test_auto_tries_directml_after_cuda_fails(
    monkeypatch: pytest.MonkeyPatch, cuda_failure: str
) -> None:
    """A failed CUDA initialization cannot hide a usable DirectML GPU."""
    attempts: list[list] = []

    class Session:
        def __init__(
            self,
            _model: str,
            *,
            sess_options: Any,  # noqa: ANN401 -- ORT options are runtime-provided
            providers: list,
            enable_fallback: bool,
        ) -> None:
            assert enable_fallback is False
            attempts.append(providers)
            name = providers[0][0]
            if name == "CUDAExecutionProvider" and cuda_failure == "exception":
                message = "CUDA failed"
                raise RuntimeError(message)
            if name == "CUDAExecutionProvider" and cuda_failure == "ort_failure":
                message = "CUDA failed"
                raise monitor.ort.capi.onnxruntime_pybind11_state.Fail(message)
            self.providers = (
                ["CPUExecutionProvider"]
                if name == "CUDAExecutionProvider"
                else [name, "CPUExecutionProvider"]
            )
            if name == "DmlExecutionProvider":
                assert sess_options.enable_mem_pattern is False
                assert (
                    sess_options.execution_mode
                    == monitor.ort.ExecutionMode.ORT_SEQUENTIAL
                )

        def get_providers(self) -> list[str]:
            return self.providers

    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    monkeypatch.setattr(monitor.ort, "InferenceSession", Session)
    result = monitor.create_session("model.onnx", 2, "auto")
    assert result.get_providers()[0] == "DmlExecutionProvider"
    assert [attempt[0][0] for attempt in attempts] == [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
    ]
    assert attempts[1][0][1]["device_id"] == 2


def test_auto_uses_cpu_after_all_gpu_providers_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode remains usable on a CPU-only host with a GPU-enabled runtime."""
    attempts: list[str] = []

    class Session:
        def __init__(
            self,
            _model: str,
            *,
            sess_options: Any,  # noqa: ANN401 -- ORT options are runtime-provided
            providers: list,
            enable_fallback: bool,
        ) -> None:
            assert enable_fallback is False
            del sess_options
            provider = providers[0]
            attempts.append(provider[0] if isinstance(provider, tuple) else provider)

        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    monkeypatch.setattr(monitor.ort, "InferenceSession", Session)
    assert monitor.create_session("model.onnx", 0, "auto").get_providers() == [
        "CPUExecutionProvider"
    ]
    assert attempts == [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_explicit_provider_disables_runtime_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later GPU failure does not silently switch the session to CPU."""

    class GpuSession:
        fallback_disabled = False

        def get_providers(self) -> list[str]:
            return ["MIGraphXExecutionProvider", "CPUExecutionProvider"]

        def disable_fallback(self) -> None:
            self.fallback_disabled = True

    session = GpuSession()
    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: ["MIGraphXExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(
        monitor.ort, "InferenceSession", lambda *_args, **_kwargs: session
    )
    assert monitor.create_session("model.onnx", 0, "migraphx") is session
    assert session.fallback_disabled


def test_self_test_reports_missing_requested_gpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed explicit request remains a machine-readable failed self-test."""
    monkeypatch.setattr(
        monitor.ort, "get_available_providers", lambda: ["CPUExecutionProvider"]
    )
    args = parse_args(["--self-test", "--model", __file__, "--device", "cuda"])
    assert monitor.run_self_test(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["device"] == "cuda"
    assert "CUDAExecutionProvider" in report["error"]


def test_self_test_reports_constructor_failure_as_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A provider constructor failure must not put fallback text before the JSON."""

    def failing_session(*_args: object, **kwargs: object) -> None:
        if kwargs.get("enable_fallback", True):
            print("EP Error: falling back to CPU")  # noqa: T201 -- simulate ORT output
        message = "CUDA initialization failed"
        raise RuntimeError(message)

    monkeypatch.setattr(
        monitor.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(monitor.ort, "InferenceSession", failing_session)
    args = parse_args(["--self-test", "--model", __file__, "--device", "cuda"])
    assert monitor.run_self_test(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "CUDA initialization failed" in report["error"]
