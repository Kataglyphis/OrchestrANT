from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Optional

from orchestrant.monitoring.gpu import PYNVML_AVAILABLE
from orchestrant.pipeline.capture import CameraCapture, OpenCVCapture
from orchestrant.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from orchestrant.pipeline.logging import configure_logging
from orchestrant.pipeline.metrics.performance import PerformanceTracker
from orchestrant.pipeline.monitoring.system import (
    SystemMonitor,
)
from orchestrant.pipeline.tracking.centroid import SimpleCentroidTracker
from orchestrant.pipeline.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
    Track,
)
from orchestrant.pipeline.ui.dearpygui import DearPyGuiViewer
from orchestrant.yolo.cli import parse_args
from orchestrant.yolo.core.constants import CLASS_NAMES, COLORS
from orchestrant.yolo.core.postprocess import postprocess
from orchestrant.yolo.core.preprocess import infer_input_size, preprocess
from orchestrant.yolo.ui.draw import (
    draw_2d_running_map,
    draw_cpu_process_history_plot,
    draw_detections,
    get_color_by_percent,
)


if TYPE_CHECKING:
    from orchestrant.pipeline.ui.wx import (
        WxPythonViewer as WxPythonViewerType,
    )

WxPythonViewer: type[WxPythonViewerType] | None = None
try:
    _wx_mod = importlib.import_module("orchestrant.pipeline.ui.wx")
    WxPythonViewer = getattr(_wx_mod, "WxPythonViewer", None)
except Exception:  # pragma: no cover - optional dependency
    WxPythonViewer = None


def run_yolo_monitor(argv: list[str] | None = None) -> int:
    """Run the YOLO monitor entry point via lazy import."""
    module = importlib.import_module("orchestrant.yolo.monitor")
    return module.run_yolo_monitor(argv)


__all__ = [
    "CLASS_NAMES",
    "COLORS",
    "PYNVML_AVAILABLE",
    "CameraCapture",
    "CameraConfig",
    "CaptureBackend",
    "DearPyGuiViewer",
    "GStreamerSubprocessCapture",
    "OpenCVCapture",
    "PerformanceMetrics",
    "PerformanceTracker",
    "SimpleCentroidTracker",
    "SystemMonitor",
    "SystemStats",
    "Track",
    "WxPythonViewer",
    "configure_logging",
    "draw_2d_running_map",
    "draw_cpu_process_history_plot",
    "draw_detections",
    "find_gstreamer_launch",
    "get_color_by_percent",
    "get_gstreamer_env",
    "infer_input_size",
    "parse_args",
    "postprocess",
    "preprocess",
    "run_yolo_monitor",
]
