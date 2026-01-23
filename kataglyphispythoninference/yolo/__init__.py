from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Optional

from kataglyphispythoninference.pipeline.capture import CameraCapture, OpenCVCapture
from kataglyphispythoninference.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from kataglyphispythoninference.pipeline.logging import configure_logging
from kataglyphispythoninference.pipeline.metrics.performance import PerformanceTracker
from kataglyphispythoninference.pipeline.monitoring.system import (
    PYNVML_AVAILABLE,
    SystemMonitor,
)
from kataglyphispythoninference.pipeline.tracking.centroid import SimpleCentroidTracker
from kataglyphispythoninference.pipeline.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
    Track,
)
from kataglyphispythoninference.pipeline.ui.dearpygui import DearPyGuiViewer
from kataglyphispythoninference.yolo.cli import parse_args
from kataglyphispythoninference.yolo.core.constants import CLASS_NAMES, COLORS
from kataglyphispythoninference.yolo.core.postprocess import postprocess
from kataglyphispythoninference.yolo.core.preprocess import infer_input_size, preprocess
from kataglyphispythoninference.yolo.ui.draw import (
    draw_2d_running_map,
    draw_cpu_process_history_plot,
    draw_detections,
    get_color_by_percent,
)


if TYPE_CHECKING:
    from kataglyphispythoninference.pipeline.ui.wx import (
        WxPythonViewer as WxPythonViewerType,
    )

WxPythonViewer: type[WxPythonViewerType] | None = None
try:
    _wx_mod = importlib.import_module("kataglyphispythoninference.pipeline.ui.wx")
    WxPythonViewer = getattr(_wx_mod, "WxPythonViewer", None)
except Exception:  # pragma: no cover - optional dependency
    WxPythonViewer = None


def run_yolo_monitor(*args: object, **kwargs: object) -> int:
    """Run the YOLO monitor entry point via lazy import."""
    module = importlib.import_module("kataglyphispythoninference.yolo.monitor")
    return module.run_yolo_monitor(*args, **kwargs)


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
