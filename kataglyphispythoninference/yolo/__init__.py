from __future__ import annotations

from kataglyphispythoninference.yolo.capture import CameraCapture, OpenCVCapture
from kataglyphispythoninference.yolo.cli import parse_args
from kataglyphispythoninference.yolo.constants import CLASS_NAMES, COLORS
from kataglyphispythoninference.yolo.draw import (
    draw_2d_running_map,
    draw_cpu_process_history_plot,
    draw_detections,
    get_color_by_percent,
)
from kataglyphispythoninference.yolo.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from kataglyphispythoninference.yolo.logging import configure_logging
from kataglyphispythoninference.yolo.performance import PerformanceTracker
from kataglyphispythoninference.yolo.postprocess import postprocess
from kataglyphispythoninference.yolo.preprocess import infer_input_size, preprocess
from kataglyphispythoninference.yolo.system import PYNVML_AVAILABLE, SystemMonitor
from kataglyphispythoninference.yolo.tracking import SimpleCentroidTracker
from kataglyphispythoninference.yolo.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
    Track,
)
import importlib
from typing import Optional, TYPE_CHECKING

from kataglyphispythoninference.yolo.viewer import DearPyGuiViewer


if TYPE_CHECKING:
    from kataglyphispythoninference.yolo.wx_viewer import (
        WxPythonViewer as WxPythonViewerType,
    )

WxPythonViewer: Optional[type["WxPythonViewerType"]] = None
try:
    _wx_mod = importlib.import_module("kataglyphispythoninference.yolo.wx_viewer")
    WxPythonViewer = getattr(_wx_mod, "WxPythonViewer", None)
except Exception:  # pragma: no cover - optional dependency
    WxPythonViewer = None


def run_yolo_monitor(*args, **kwargs):
    from kataglyphispythoninference.yolo.monitor import run_yolo_monitor as _run

    return _run(*args, **kwargs)


__all__ = [
    "run_yolo_monitor",
    "configure_logging",
    "parse_args",
    "CLASS_NAMES",
    "COLORS",
    "CameraConfig",
    "CaptureBackend",
    "PerformanceMetrics",
    "SystemStats",
    "Track",
    "CameraCapture",
    "OpenCVCapture",
    "GStreamerSubprocessCapture",
    "find_gstreamer_launch",
    "get_gstreamer_env",
    "SystemMonitor",
    "PYNVML_AVAILABLE",
    "PerformanceTracker",
    "infer_input_size",
    "preprocess",
    "postprocess",
    "SimpleCentroidTracker",
    "draw_2d_running_map",
    "draw_cpu_process_history_plot",
    "draw_detections",
    "get_color_by_percent",
    "DearPyGuiViewer",
    "WxPythonViewer",
]
