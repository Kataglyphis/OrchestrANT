"""Unit tests for yolo-monitor's argument defaults and execution-provider choice."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest


# monitor imports OpenCV and ONNX Runtime, which the free-threaded 3.14t leg does not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

from orchestrant import paths
from orchestrant.yolo import (
    cuda_probe,
    hip_probe,
    monitor,
    plugin_ep,
    providers as providers_module,
)
from orchestrant.yolo.cli import parse_args


@pytest.fixture(autouse=True)
def _kernel_probe_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session fakes below count sessions: only the kernel probe's own tests see its session."""
    monkeypatch.setattr(providers_module, "require_kernel", lambda _gpu: None)


@pytest.fixture(autouse=True)
def _no_plugin_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here finds a MIGraphX module unless it places one, whatever image runs the suite."""
    monkeypatch.setenv("ORT_AMDGPU_EP_ROOT", "")
    monkeypatch.setattr(plugin_ep, "_registered", set())
    monkeypatch.setattr(plugin_ep, "_failures", {})


def _plugin_module(root: paths.Path, monkeypatch: pytest.MonkeyPatch) -> paths.Path:
    """Place a stand-in for the image's MIGraphX module under ``root`` and name the root."""
    module = root / "lib" / "libmigraphx-ep.so"
    module.parent.mkdir(parents=True)
    module.write_bytes(b"stand-in")
    monkeypatch.setenv("ORT_AMDGPU_EP_ROOT", str(root))
    return module


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


def test_the_score_threshold_defaults_to_the_familys_one_value() -> None:
    """One picture gave different results per product while this default was 0.5 and the cameras' 0.25."""
    assert parse_args([]).conf == 0.25
    assert parse_args(["--conf", "0.6"]).conf == 0.6


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
    ("available", "module", "expected"),
    [
        (["CPUExecutionProvider"], False, ["CPUExecutionProvider"]),
        (
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            False,
            ["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
        (["CPUExecutionProvider"], True, ["MIGraphX", "CPUExecutionProvider"]),
        (
            ["CPUExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"],
            True,
            [
                "CUDAExecutionProvider",
                "MIGraphX",
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            ],
        ),
    ],
)
def test_providers_follow_the_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: paths.Path,
    available: list[str],
    module: bool,  # noqa: FBT001 -- pytest hands the parametrized value over by name
    expected: list[str],
) -> None:
    """Only providers this ORT build has, and MIGraphX where its module is on disk, in the app's order."""
    monkeypatch.setattr(
        providers_module.ort, "get_available_providers", lambda: available
    )
    if module:
        _plugin_module(tmp_path, monkeypatch)
    names = [p[0] if isinstance(p, tuple) else p for p in monitor.select_providers(0)]
    assert names == expected


def test_the_old_migraphx_name_in_the_build_selects_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-tree MIGraphX provider is never used: the plugin's module is the only way in."""
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["MIGraphXExecutionProvider", "CPUExecutionProvider"],
    )
    assert monitor.select_providers(0) == ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError, match="MIGraphX was not found"):
        monitor.select_providers(0, "migraphx")


@pytest.mark.parametrize(
    ("device", "provider"),
    [
        ("cuda", "CUDAExecutionProvider"),
        ("directml", "DmlExecutionProvider"),
    ],
)
def test_explicit_gpu_requires_the_requested_provider(
    monkeypatch: pytest.MonkeyPatch, device: str, provider: str
) -> None:
    """An explicit GPU choice never accepts a runtime without its provider."""
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CPUExecutionProvider"],
    )
    with pytest.raises(RuntimeError, match=provider):
        monitor.select_providers(2, device)
    monkeypatch.setattr(
        providers_module.ort,
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
        providers_module.ort,
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
    monkeypatch.setattr(
        providers_module.ort, "get_available_providers", lambda: available
    )

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
                    == providers_module.ort.ExecutionMode.ORT_SEQUENTIAL
                )
            else:
                assert sess_options.enable_mem_pattern is True
                assert (
                    sess_options.intra_op_num_threads
                    == providers_module.shipped_intra_op_threads()
                )
            self.providers = [p[0] if isinstance(p, tuple) else p for p in providers]

        def get_providers(self) -> list[str]:
            return self.providers

        def disable_fallback(self) -> None:
            pass

    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    providers_module.create_session("model.onnx", 0, device)


@pytest.mark.parametrize(
    ("device", "provider"),
    [
        ("cuda", "CUDAExecutionProvider"),
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
        providers_module.ort,
        "get_available_providers",
        lambda: [provider, "CPUExecutionProvider"],
    )
    monkeypatch.setattr(
        providers_module.ort, "InferenceSession", lambda *_args, **_kwargs: CpuSession()
    )
    with pytest.raises(RuntimeError, match=f"did not activate {provider}"):
        providers_module.create_session("model.onnx", 0, device)


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
                raise providers_module.ort.capi.onnxruntime_pybind11_state.Fail(message)
            self.providers = (
                ["CPUExecutionProvider"]
                if name == "CUDAExecutionProvider"
                else [name, "CPUExecutionProvider"]
            )
            if name == "DmlExecutionProvider":
                assert sess_options.enable_mem_pattern is False
                assert (
                    sess_options.execution_mode
                    == providers_module.ort.ExecutionMode.ORT_SEQUENTIAL
                )

        def get_providers(self) -> list[str]:
            return self.providers

    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    result = providers_module.create_session("model.onnx", 2, "auto")
    assert result.get_providers()[0] == "DmlExecutionProvider"
    assert [attempt[0][0] for attempt in attempts] == [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
    ]
    assert attempts[1][0][1]["device_id"] == 2


def test_auto_retry_preserves_the_requested_profile_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profiling follows the GPU that survives auto provider initialization."""
    attempts = []

    class Session:
        def __init__(self, _model: str, **kwargs: Any) -> None:  # noqa: ANN401 -- runtime ORT options
            options = kwargs["sess_options"]
            assert options.enable_profiling is True
            assert options.profile_file_prefix == "profile"
            provider = kwargs["providers"][0]
            attempts.append(provider)
            if provider[0] == "CUDAExecutionProvider":
                msg = "CUDA unavailable"
                raise RuntimeError(msg)
            assert options.enable_mem_pattern is False
            assert (
                options.execution_mode
                == providers_module.ort.ExecutionMode.ORT_SEQUENTIAL
            )

        def get_providers(self) -> list[str]:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]

    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    providers_module.create_session("model.onnx", 2, "auto", profile_prefix="profile")
    assert [provider[0] for provider in attempts] == [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
    ]
    assert attempts[1][1]["device_id"] == 2


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
        providers_module.ort,
        "get_available_providers",
        lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    assert providers_module.create_session("model.onnx", 0, "auto").get_providers() == [
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
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        def disable_fallback(self) -> None:
            self.fallback_disabled = True

    session = GpuSession()
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(
        providers_module.ort, "InferenceSession", lambda *_args, **_kwargs: session
    )
    assert providers_module.create_session("model.onnx", 0, "cuda") is session
    assert session.fallback_disabled


def _migraphx_gpu_that_never_answers(
    root: paths.Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """The MIGraphX module on a GPU that hangs; returns the registrations and the sessions' providers."""
    asked: list[str] = []

    class Session:
        def __init__(
            self, _model: str, *, providers: list[str], **_options: object
        ) -> None:
            self.providers = providers
            asked.append(providers[0])

        def get_providers(self) -> list[str]:
            return self.providers

    _plugin_module(root, monkeypatch)
    # The host's own providers stay out: on Windows the real list holds DirectML, which auto asks first.
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setattr(hip_probe.sys, "platform", "linux")
    monkeypatch.setattr(hip_probe, "ANSWER_LIMIT_SECONDS", 0.1)
    monkeypatch.setattr(hip_probe, "COPY_TO_DEVICE", "import time; time.sleep(60)")
    monkeypatch.setattr(
        providers_module.ort,
        "register_execution_provider_library",
        lambda name, _path: asked.append(f"register {name}"),
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    return asked


def test_a_named_migraphx_fails_when_the_gpu_does_not_answer(
    tmp_path: paths.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung GPU is an error naming the way out, before the registration that would wait for it."""
    asked = _migraphx_gpu_that_never_answers(tmp_path, monkeypatch)
    with pytest.raises(
        RuntimeError, match=r"MIGraphX cannot run.*--device cpu runs on the CPU"
    ):
        providers_module.create_session("model.onnx", 0, "migraphx")
    assert asked == []


def test_auto_moves_on_when_the_gpu_does_not_answer(
    tmp_path: paths.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode treats a hung GPU like any other unusable provider."""
    asked = _migraphx_gpu_that_never_answers(tmp_path, monkeypatch)
    session = providers_module.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    assert asked == ["CPUExecutionProvider"]


def test_the_copy_passes_without_a_usable_device() -> None:
    """No HIP runtime, or a device index it does not have, is for MIGraphX to report."""
    command = [sys.executable, "-c", hip_probe.COPY_TO_DEVICE, "999"]
    assert hip_probe.answers_within(60.0, command)


NO_KERNEL_ERROR = (
    "[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while running Relu node. "
    "Status Message: CUDA error cudaErrorNoKernelImageForDevice:no kernel image is available"
)


def _cuda_card_without_a_kernel(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A runtime with CUDA on a card it has no kernel for; returns the model sessions asked for."""
    asked: list[str] = []

    class Session:
        def __init__(
            self, model: object, *, providers: list[Any], **_options: object
        ) -> None:
            self.providers = [p[0] if isinstance(p, tuple) else p for p in providers]
            if model != cuda_probe.ONE_KERNEL:
                asked.append(self.providers[0])

        def run(self, *_args: object) -> None:
            raise RuntimeError(NO_KERNEL_ERROR)

        def get_providers(self) -> list[str]:
            return self.providers

    monkeypatch.setattr(providers_module, "require_kernel", cuda_probe.require_kernel)
    monkeypatch.setattr(
        cuda_probe,
        "card_name",
        lambda gpu: f"RTX 2080 on {gpu} (compute capability 7.5)",
    )
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    return asked


def test_a_named_cuda_fails_naming_a_card_without_a_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No kernel is an error naming the card and the way out, before the real session."""
    asked = _cuda_card_without_a_kernel(monkeypatch)
    with pytest.raises(RuntimeError) as error:
        providers_module.create_session("model.onnx", 3, "cuda")
    assert "RTX 2080 on 3 (compute capability 7.5)" in str(error.value)
    assert "--device cpu runs on the CPU" in str(error.value)
    assert asked == []


def test_auto_runs_on_the_cpu_on_a_card_without_a_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode treats a card without a kernel like any other unusable provider."""
    asked = _cuda_card_without_a_kernel(monkeypatch)
    session = providers_module.create_session("model.onnx", 0, "auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    assert asked == ["CPUExecutionProvider"]


def test_only_a_missing_kernel_stops_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every other probe failure is for the real session to report."""

    def fail(_providers: object) -> float:
        message = "CUDA failure 999: Unknown error"
        raise RuntimeError(message)

    monkeypatch.setattr(cuda_probe, "run_one_kernel", fail)
    cuda_probe.require_kernel(0)


def test_the_kernel_model_runs_on_the_cpu() -> None:
    """The embedded model is valid and really computes: the probe proves nothing otherwise."""
    assert cuda_probe.run_one_kernel(["CPUExecutionProvider"]) == 0.0


def test_no_driver_or_one_without_the_symbols_names_nothing() -> None:
    """A missing kernel still fails when the driver will not name the card."""
    assert cuda_probe.card_name(0, "libnot-a-cuda-driver.so") is None
    if sys.platform == "linux":
        assert cuda_probe.card_name(0, "libc.so.6") is None


def test_a_child_that_cannot_start_or_crashes_counts_as_an_answer() -> None:
    """Only a timeout says the GPU hangs."""
    assert hip_probe.answers_within(60.0, ["/no/such/interpreter"])
    assert hip_probe.answers_within(60.0, [sys.executable, "-c", "raise SystemExit(3)"])


def test_self_test_reports_missing_requested_gpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed explicit request remains a machine-readable failed self-test."""
    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CPUExecutionProvider"],
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
        providers_module.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", failing_session)
    args = parse_args(["--self-test", "--model", __file__, "--device", "cuda"])
    assert monitor.run_self_test(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "CUDA initialization failed" in report["error"]


def test_threads_default_to_ort_and_cli_beats_config(tmp_path: paths.Path) -> None:
    """No flag leaves the shipped default; --threads overrides [onnxruntime] intra_op_threads."""
    config = tmp_path / "yolo.toml"
    config.write_text("[onnxruntime]\nintra_op_threads = 6\n", encoding="utf-8")
    assert parse_args([]).intra_op_threads is None
    assert parse_args(["--threads", "0"]).intra_op_threads == 0
    assert parse_args(["--config", str(config)]).intra_op_threads == 6
    assert parse_args(["--config", str(config), "--threads", "1"]).intra_op_threads == 1


@pytest.mark.parametrize(
    "body",
    [
        "onnxruntime = 4\n",
        "[onnxruntime]\nintra_op_threads = -1\n",
        "[onnxruntime]\nintra_op_threads = true\n",
        "[",
    ],
)
def test_a_bad_thread_config_is_refused(tmp_path: paths.Path, body: str) -> None:
    """A malformed table, a negative or boolean count, or broken TOML stops before any session."""
    config = tmp_path / "yolo.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit):
        parse_args(["--config", str(config)])
    with pytest.raises(SystemExit):
        parse_args(["--threads", "-2"])


def test_the_shipped_thread_default_leaves_a_cpu_free_up_to_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wide or a narrow affinity mask decides the default, not the machine's size."""
    for mask, expected in ((range(32), 8), (range(8), 7), (range(3), 2), (range(1), 1)):
        monkeypatch.setattr(
            providers_module.os,
            "sched_getaffinity",
            lambda _pid, m=mask: set(m),
            raising=False,
        )
        assert providers_module.shipped_intra_op_threads() == expected


def test_the_thread_count_reaches_intra_op_num_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every session the monitor creates carries the requested intra-op thread count."""
    seen: list[int] = []

    class Session:
        def __init__(self, _model: str, **kwargs: object) -> None:
            options = kwargs["sess_options"]
            assert isinstance(options, providers_module.ort.SessionOptions)
            seen.append(options.intra_op_num_threads)

        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

        def disable_fallback(self) -> None:
            """Explicit requests disable fallback; nothing to record."""

    monkeypatch.setattr(
        providers_module.ort,
        "get_available_providers",
        lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setattr(providers_module.ort, "InferenceSession", Session)
    providers_module.create_session("model.onnx", 0, "cpu", intra_op_threads=5)
    providers_module.create_session("model.onnx", 0, "auto", intra_op_threads=7)
    providers_module.create_session("model.onnx", 0, "cpu")
    assert seen == [5, 7, providers_module.shipped_intra_op_threads()]
    with pytest.raises(ValueError, match="intra_op_threads"):
        providers_module.create_session("model.onnx", 0, "cpu", intra_op_threads=-1)
