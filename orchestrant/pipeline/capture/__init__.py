"""Camera capture backends for monitoring pipelines."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from orchestrant.pipeline.capture.core import CameraCapture, CaptureProtocol
    from orchestrant.pipeline.capture.gstreamer import (
        GStreamerSubprocessCapture,
        find_gstreamer_launch,
        get_gstreamer_env,
    )
    from orchestrant.pipeline.capture.opencv import OpenCVCapture

# Lazy (PEP 562): the GStreamer backend needs no cv2, which has no free-threaded (3.14t) wheel.
_EXPORTS = {
    "CameraCapture": "orchestrant.pipeline.capture.core",
    "CaptureProtocol": "orchestrant.pipeline.capture.core",
    "GStreamerSubprocessCapture": "orchestrant.pipeline.capture.gstreamer",
    "find_gstreamer_launch": "orchestrant.pipeline.capture.gstreamer",
    "get_gstreamer_env": "orchestrant.pipeline.capture.gstreamer",
    "OpenCVCapture": "orchestrant.pipeline.capture.opencv",
}


def __getattr__(name: str) -> object:
    """Resolve a public name from its home module on first access."""
    if name in _EXPORTS:
        return getattr(importlib.import_module(_EXPORTS[name]), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Advertise lazy exports to introspection alongside real attributes."""
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "CameraCapture",
    "CaptureProtocol",
    "GStreamerSubprocessCapture",
    "OpenCVCapture",
    "find_gstreamer_launch",
    "get_gstreamer_env",
]
