"""Monitoring pipeline package: capture, tracking, metrics, and viewers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


# Lazy (PEP 562): a light submodule import must not pull in cv2, Flet or GStreamer.
_EXPORTS = {
    "CameraCapture": "orchestrant.pipeline.capture",
    "OpenCVCapture": "orchestrant.pipeline.capture",
    "GStreamerSubprocessCapture": "orchestrant.pipeline.capture.gstreamer",
    "find_gstreamer_launch": "orchestrant.pipeline.capture.gstreamer",
    "get_gstreamer_env": "orchestrant.pipeline.capture.gstreamer",
    "attach_log_buffer": "orchestrant.pipeline.logging",
    "configure_logging": "orchestrant.pipeline.logging",
    "create_log_buffer": "orchestrant.pipeline.logging",
    "log_buffer_lines": "orchestrant.pipeline.logging",
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
    "FletViewer": "orchestrant.pipeline.ui.flet_viewer",
    "AMD_AVAILABLE": "orchestrant.monitoring.gpu",
    "PYNVML_AVAILABLE": "orchestrant.monitoring.gpu",
}


def __getattr__(name: str) -> object:
    """Resolve a public name from its home module on first access."""
    if name in _EXPORTS:
        module = importlib.import_module(_EXPORTS[name])
        return getattr(module, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Advertise lazy exports to introspection alongside real attributes."""
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "AMD_AVAILABLE",
    "PYNVML_AVAILABLE",
    "CameraCapture",
    "CameraConfig",
    "CaptureBackend",
    "FletViewer",
    "GStreamerSubprocessCapture",
    "OpenCVCapture",
    "PerformanceMetrics",
    "PerformanceTracker",
    "PowerMonitor",
    "SimpleCentroidTracker",
    "SystemMonitor",
    "SystemStats",
    "Track",
    "attach_log_buffer",
    "configure_logging",
    "create_log_buffer",
    "find_gstreamer_launch",
    "get_cpu_freq_ratio",
    "get_gstreamer_env",
    "log_buffer_lines",
]
