from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from kataglyphispythoninference.pipeline.capture import CameraCapture, OpenCVCapture
from kataglyphispythoninference.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
    find_gstreamer_launch,
    get_gstreamer_env,
)
from kataglyphispythoninference.pipeline.logging import (
    attach_log_buffer,
    configure_logging,
    create_log_buffer,
)
from kataglyphispythoninference.pipeline.metrics.performance import PerformanceTracker
from kataglyphispythoninference.pipeline.monitoring.power import (
    PowerMonitor,
    get_cpu_freq_ratio,
)
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
