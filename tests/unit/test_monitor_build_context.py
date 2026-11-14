"""Monitor start-up: what a failed step stops and releases; no camera, model or GPU is touched."""

from __future__ import annotations

import argparse
import json
from collections import deque
from types import SimpleNamespace

import numpy as np
import pytest


# monitor imports OpenCV and ONNX Runtime, which the free-threaded leg may not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="the chain ONNX Runtime is GIL-only")

from orchestrant.yolo import monitor, self_test, startup
from orchestrant.yolo.core import detector


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


def _session(metadata: dict[str, str] | None = None) -> SimpleNamespace:
    model_input = SimpleNamespace(name="x", shape=[1, 3, 320, 320])
    meta = SimpleNamespace(custom_metadata_map=metadata or {})
    return SimpleNamespace(
        get_providers=lambda: ["CPUExecutionProvider"],
        get_inputs=lambda: [model_input],
        get_modelmeta=lambda: meta,
    )


def _names(wired: pytest.MonkeyPatch, layout: str) -> SimpleNamespace:
    """Start-up opens a model whose metadata names this output layout."""
    session = _session({"kataglyphis.output": layout})
    for module in (startup, self_test):
        wired.setattr(module, "session_for", lambda _args, _model: session)
    return session


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Replace every part start-up would touch: camera, model, GPU, window."""
    _Camera.opens = True
    _started.clear()
    monkeypatch.setattr(startup, "find_gstreamer_launch", lambda: (None, "absent"))
    monkeypatch.setattr(startup, "SystemMonitor", _Monitor)
    monkeypatch.setattr(startup, "CameraCapture", _Camera)
    for module in (startup, self_test):
        monkeypatch.setattr(module, "session_for", lambda _args, _model: _session())
    monkeypatch.setattr(detector, "wants_stretch", lambda _session: False)
    monkeypatch.setattr(startup, "_log_platform_info", lambda _stats: None)
    monkeypatch.setattr(startup, "_get_cpu_model", lambda: "cpu")
    monkeypatch.setattr(startup, "PowerMonitor", lambda: "power")
    monkeypatch.setattr(startup, "_init_viewer", lambda *_args: None)
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
    wired.setattr(startup, "session_for", _no_model)
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
    assert ctx.detector.input_name == "x"
    assert ctx.cpu_tdp_watts == 45.0
    assert ctx.cpu_history.maxlen == 2
    assert ctx.log_buffer is log_buffer
    assert ctx.hardware_info["cpu_model"] == "cpu"
    assert _started[-1].shut_down == 0
    assert ctx.tracks == {}
    assert ctx.detector.layout is None


def test_context_carries_the_layout_the_model_names(wired: pytest.MonkeyPatch) -> None:
    """The layout is read once, when the session opens, and every frame is decoded by it."""
    _names(wired, "cxcywh_scores_by_anchor")
    ctx = monitor.build_context(_args(), deque())
    assert ctx.detector.layout == "cxcywh_scores_by_anchor"


def test_an_unknown_output_layout_stops_the_start_and_names_the_value(
    wired: pytest.MonkeyPatch,
) -> None:
    """A model that names a layout nobody decodes never reaches the camera."""
    _names(wired, "yolo_v99")
    with pytest.raises(monitor.MonitorInitError, match="Failed to load model") as err:
        monitor.build_context(_args(), deque())
    assert "'yolo_v99'" in str(err.value.__cause__)
    assert _started[-1].shut_down == 1


def test_the_self_test_fails_on_an_unknown_output_layout_and_names_the_value(
    wired: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The install check says why in its JSON, before it runs the model."""
    _names(wired, "yolo_v99")
    args = argparse.Namespace(model=__file__, device="cpu")
    assert monitor.run_self_test(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "'yolo_v99'" in report["error"]


def test_the_self_test_decodes_by_the_layout_the_model_names(
    wired: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 7 by 3 anchor head holds two boxes here; guessed, it holds none."""
    head = np.zeros((1, 7, 3), dtype=np.float32)
    head[0, :4] = 100.0
    head[0, 4, 0] = head[0, 5, 1] = 0.9
    session = _names(wired, "cxcywh_scores_by_anchor")
    session.run = lambda _outputs, _feeds: [head]
    args = monitor.parse_args(["--self-test", "--model", __file__, "--device", "cpu"])
    assert monitor.run_self_test(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["detections_on_blank_frame"] == 2
