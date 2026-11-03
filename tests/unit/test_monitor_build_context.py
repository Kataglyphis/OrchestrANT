"""Monitor start-up: what a failed step stops and releases; no camera, model or GPU is touched."""

from __future__ import annotations

import argparse
from collections import deque
from types import SimpleNamespace

import pytest


# monitor imports OpenCV and ONNX Runtime, which the free-threaded leg may not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

from orchestrant.yolo import monitor


_started: list[_Monitor] = []


class _Monitor:
    def __init__(self, *, gpu_device_id: int) -> None:
        self.gpu_device_id = gpu_device_id
        self.shut_down = 0
        _started.append(self)

    def get_stats(self) -> SimpleNamespace:
        return SimpleNamespace(
            ram_total_gb=8.0, gpu_name="N/A", gpu_memory_total_gb=0.0
        )

    def shutdown(self) -> None:
        self.shut_down += 1


class _Camera:
    opens = True

    def __init__(self, config: object) -> None:
        self.config = config

    def open(self) -> bool:
        return _Camera.opens

    def get_info(self) -> dict[str, str]:
        return {"backend": "opencv"}


def _args(backend: str = "opencv") -> argparse.Namespace:
    return argparse.Namespace(
        backend=backend,
        camera=0,
        width=640,
        height=480,
        fps=30,
        ui="opencv",
        gpu=0,
        model="m.onnx",
        cpu_history=1,
    )


def _session() -> SimpleNamespace:
    model_input = SimpleNamespace(name="x", shape=[1, 3, 320, 320])
    return SimpleNamespace(
        get_providers=lambda: ["CPUExecutionProvider"],
        get_inputs=lambda: [model_input],
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Replace every part start-up would touch: camera, model, GPU, window."""
    _Camera.opens = True
    _started.clear()
    monkeypatch.setattr(monitor, "find_gstreamer_launch", lambda: (None, "absent"))
    monkeypatch.setattr(monitor, "SystemMonitor", _Monitor)
    monkeypatch.setattr(monitor, "CameraCapture", _Camera)
    monkeypatch.setattr(monitor, "session_for", lambda _args, _model: _session())
    monkeypatch.setattr(monitor, "wants_stretch", lambda _session: False)
    monkeypatch.setattr(monitor, "_log_platform_info", lambda _stats: None)
    monkeypatch.setattr(monitor, "_get_cpu_model", lambda: "cpu")
    monkeypatch.setattr(monitor, "PowerMonitor", lambda: "power")
    monkeypatch.setattr(monitor, "_init_viewer", lambda *_args: None)
    return monkeypatch


def _no_model(_args: object, _model: object) -> None:
    message = "no model"
    raise OSError(message)


@pytest.mark.usefixtures("wired")
def test_missing_gstreamer_stops_before_the_system_monitor() -> None:
    """A backend that cannot run starts nothing."""
    with pytest.raises(monitor.MonitorInitError, match="GStreamer backend requested"):
        monitor.build_context(_args("gstreamer"), deque())
    assert _started == []


def test_failed_model_load_shuts_the_monitor_down(wired: pytest.MonkeyPatch) -> None:
    """The cause stays attached and the system monitor's threads end."""
    wired.setattr(monitor, "session_for", _no_model)
    with pytest.raises(monitor.MonitorInitError, match="Failed to load model") as err:
        monitor.build_context(_args(), deque())
    assert isinstance(err.value.__cause__, OSError)
    assert _started[-1].shut_down == 1


@pytest.mark.usefixtures("wired")
def test_failed_camera_shuts_the_monitor_down() -> None:
    """The system monitor's threads end when the camera does not open."""
    _Camera.opens = False
    with pytest.raises(monitor.MonitorInitError, match="Failed to open camera"):
        monitor.build_context(_args(), deque())
    assert _started[-1].shut_down == 1


def test_context_carries_each_part(wired: pytest.MonkeyPatch) -> None:
    """A clean start keeps the monitor running and fills the context."""
    wired.setenv("KATAGLYPHIS_CPU_TDP_WATTS", "45")
    log_buffer: deque[str] = deque()
    ctx = monitor.build_context(_args(), log_buffer)
    assert ctx.input_name == "x"
    assert ctx.cpu_tdp_watts == 45.0
    assert ctx.cpu_history.maxlen == 2
    assert ctx.log_buffer is log_buffer
    assert ctx.hardware_info["cpu_model"] == "cpu"
    assert _started[-1].shut_down == 0
    assert ctx.tracks == {}
