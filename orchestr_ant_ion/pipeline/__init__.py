from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from orchestr_ant_ion.pipeline.capture import CameraCapture, OpenCVCapture
from orchestr_ant_ion.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from orchestr_ant_ion.pipeline.logging import (
    attach_log_buffer,
    configure_logging,
    create_log_buffer,
)
from orchestr_ant_ion.pipeline.metrics.performance import PerformanceTracker
from orchestr_ant_ion.pipeline.monitoring.power import (
    PowerMonitor,
    get_cpu_freq_ratio,
)
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


__all__ = [
    "PYNVML_AVAILABLE",
    "CameraCapture",
    "CameraConfig",
    "CaptureBackend",
    "DearPyGuiViewer",
    "GStreamerSubprocessCapture",
    "OpenCVCapture",
    "PerformanceMetrics",
    "PerformanceTracker",
    "PowerMonitor",
    "SimpleCentroidTracker",
    "SystemMonitor",
    "SystemStats",
    "Track",
    "WxPythonViewer",
    "attach_log_buffer",
    "configure_logging",
    "create_log_buffer",
    "find_gstreamer_launch",
    "get_cpu_freq_ratio",
    "get_gstreamer_env",
]

