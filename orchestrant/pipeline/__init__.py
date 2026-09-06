"""Monitoring pipeline package: capture, tracking, metrics, and viewers.

The re-exports below resolve lazily (PEP 562): importing a light submodule
such as :mod:`orchestrant.pipeline.types` must not drag in the heavy
optional runtime (cv2, DearPyGui, GStreamer helpers) that other submodules
need. Consumers keep the flat ``from orchestrant.pipeline import X``
API; each name imports its home module only when first accessed.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from orchestrant.pipeline.ui.wx import (
        WxPythonViewer as WxPythonViewerType,
    )

# name -> home module (relative to this package's parent)
_EXPORTS = {
    "CameraCapture": "orchestrant.pipeline.capture",
    "OpenCVCapture": "orchestrant.pipeline.capture",
    "GStreamerSubprocessCapture": "orchestrant.pipeline.capture.gstreamer",
    "find_gstreamer_launch": "orchestrant.pipeline.capture.gstreamer",
    "get_gstreamer_env": "orchestrant.pipeline.capture.gstreamer",
    "attach_log_buffer": "orchestrant.pipeline.logging",
    "configure_logging": "orchestrant.pipeline.logging",
    "create_log_buffer": "orchestrant.pipeline.logging",
    "PerformanceTracker": "orchestrant.pipeline.metrics.performance",
    "PowerMonitor": "orchestrant.pipeline.monitoring.power",
    "get_cpu_freq_ratio": "orchestrant.pipeline.monitoring.power",
    "SystemMonitor": "orchestrant.pipeline.monitoring.system",
    "SimpleCentroidTracker": "orchestrant.pipeline.tracking.centroid",
    "CameraConfig": "orchestrant.pipeline.types",
    "CaptureBackend": "orchestrant.pipeline.types",
    "PerformanceMetrics": "orchestrant.pipeline.types",
    "SystemStats": "orchestrant.pipeline.types",
    "Track": "orchestrant.pipeline.types",
    "DearPyGuiViewer": "orchestrant.pipeline.ui.dearpygui",
    "PYNVML_AVAILABLE": "orchestrant.monitoring.gpu",
}


def __getattr__(name: str) -> object:
    """Resolve a public name from its home module on first access."""
    if name == "WxPythonViewer":
        # Optional dependency: preserved contract is a None sentinel, not an
        # ImportError, when wxPython is absent (yolo/monitor.py checks None).
        try:
            _wx_mod = importlib.import_module("orchestrant.pipeline.ui.wx")
        except Exception:  # pragma: no cover - optional dependency
            return None
        return getattr(_wx_mod, "WxPythonViewer", None)
    if name in _EXPORTS:
        module = importlib.import_module(_EXPORTS[name])
        return getattr(module, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Advertise lazy exports to introspection alongside real attributes."""
    return sorted(set(globals()) | set(__all__))


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
