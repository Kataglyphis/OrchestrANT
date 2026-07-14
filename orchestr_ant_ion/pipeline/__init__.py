"""Monitoring pipeline package: capture, tracking, metrics, and viewers.

The re-exports below resolve lazily (PEP 562): importing a light submodule
such as :mod:`orchestr_ant_ion.pipeline.types` must not drag in the heavy
optional runtime (cv2, DearPyGui, GStreamer helpers) that other submodules
need. Consumers keep the flat ``from orchestr_ant_ion.pipeline import X``
API; each name imports its home module only when first accessed.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from orchestr_ant_ion.pipeline.ui.wx import (
        WxPythonViewer as WxPythonViewerType,
    )

# name -> home module (relative to this package's parent)
_EXPORTS = {
    "CameraCapture": "orchestr_ant_ion.pipeline.capture",
    "OpenCVCapture": "orchestr_ant_ion.pipeline.capture",
    "GStreamerSubprocessCapture": "orchestr_ant_ion.pipeline.capture.gstreamer",
    "find_gstreamer_launch": "orchestr_ant_ion.pipeline.capture.gstreamer",
    "get_gstreamer_env": "orchestr_ant_ion.pipeline.capture.gstreamer",
    "attach_log_buffer": "orchestr_ant_ion.pipeline.logging",
    "configure_logging": "orchestr_ant_ion.pipeline.logging",
    "create_log_buffer": "orchestr_ant_ion.pipeline.logging",
    "PerformanceTracker": "orchestr_ant_ion.pipeline.metrics.performance",
    "PowerMonitor": "orchestr_ant_ion.pipeline.monitoring.power",
    "get_cpu_freq_ratio": "orchestr_ant_ion.pipeline.monitoring.power",
    "SystemMonitor": "orchestr_ant_ion.pipeline.monitoring.system",
    "SimpleCentroidTracker": "orchestr_ant_ion.pipeline.tracking.centroid",
    "CameraConfig": "orchestr_ant_ion.pipeline.types",
    "CaptureBackend": "orchestr_ant_ion.pipeline.types",
    "PerformanceMetrics": "orchestr_ant_ion.pipeline.types",
    "SystemStats": "orchestr_ant_ion.pipeline.types",
    "Track": "orchestr_ant_ion.pipeline.types",
    "DearPyGuiViewer": "orchestr_ant_ion.pipeline.ui.dearpygui",
    "PYNVML_AVAILABLE": "orchestr_ant_ion.monitoring.gpu",
}


def __getattr__(name: str) -> object:
    """Resolve a public name from its home module on first access."""
    if name == "WxPythonViewer":
        # Optional dependency: preserved contract is a None sentinel, not an
        # ImportError, when wxPython is absent (yolo/monitor.py checks None).
        try:
            _wx_mod = importlib.import_module("orchestr_ant_ion.pipeline.ui.wx")
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
