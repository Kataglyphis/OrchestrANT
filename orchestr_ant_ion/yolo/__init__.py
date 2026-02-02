from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Optional

from orchestr_ant_ion.pipeline.capture import CameraCapture, OpenCVCapture
from orchestr_ant_ion.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from orchestr_ant_ion.pipeline.logging import configure_logging
from orchestr_ant_ion.pipeline.metrics.performance import PerformanceTracker
from orchestr_ant_ion.pipeline.monitoring.system import (
    PYNVML_AVAILABLE,
    SystemMonitor,
)
from orchestr_ant_ion.pipeline.tracking.centroid import SimpleCentroidTracker
from orchestr_ant_ion.pipeline.types import (
    CameraConfig,
    CaptureBackend,
    PerformanceMetrics,
    SystemStats,
    Track,
)
from orchestr_ant_ion.pipeline.ui.dearpygui import DearPyGuiViewer
from orchestr_ant_ion.yolo.cli import parse_args
from orchestr_ant_ion.yolo.core.constants import CLASS_NAMES, COLORS
from orchestr_ant_ion.yolo.core.postprocess import postprocess
from orchestr_ant_ion.yolo.core.preprocess import infer_input_size, preprocess
from orchestr_ant_ion.yolo.ui.draw import (
    draw_2d_running_map,
    draw_cpu_process_history_plot,
    draw_detections,
    get_color_by_percent,
)


if TYPE_CHECKING:
    from orchestr_ant_ion.pipeline.ui.wx import (
        WxPythonViewer as WxPythonViewerType,
    )

WxPythonViewer: type[WxPythonViewerType] | None = None
try:
    _wx_mod = importlib.import_module("orchestr_ant_ion.pipeline.ui.wx")
    WxPythonViewer = getattr(_wx_mod, "WxPythonViewer", None)
except Exception:  # pragma: no cover - optional dependency
    WxPythonViewer = None


def run_yolo_monitor(*args: object, **kwargs: object) -> int:
    """Run the YOLO monitor entry point via lazy import."""
    module = importlib.import_module("orchestr_ant_ion.yolo.monitor")
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

