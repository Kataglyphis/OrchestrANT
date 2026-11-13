"""Capture orchestration helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from loguru import logger

from orchestrant.pipeline.capture.gstreamer import (
    GStreamerSubprocessCapture,
)
from orchestrant.pipeline.capture.opencv import OpenCVCapture
from orchestrant.pipeline.types import CaptureBackend


if TYPE_CHECKING:
    import numpy as np

    from orchestrant.pipeline.types import CameraConfig


class CaptureProtocol(Protocol):
    """Protocol for capture backends."""

    def open(self) -> bool:
        """Open the capture backend."""
        ...

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Read a frame from the backend."""
        ...

    def release(self) -> None:
        """Release backend resources."""
        ...

    def is_opened(self) -> bool:
        """Return True when the backend is open."""
        ...

    def get_info(self) -> dict:
        """Return backend metadata for diagnostics."""
        ...


class CameraCapture:
    """Unified camera capture supporting OpenCV and GStreamer subprocess backends."""

    def __init__(self, config: CameraConfig) -> None:
        """Create a capture wrapper with the requested backend."""
        self.config = config
        self._capture: CaptureProtocol | None = None
        self.backend_name = ""

    def open(self) -> bool:
        """Open the selected backend; a backend the user named is not replaced by another one."""
        if self.config.backend == CaptureBackend.GSTREAMER:
            logger.info("Using GStreamer subprocess capture...")
            self._capture = GStreamerSubprocessCapture(self.config)
            if self._capture.open():
                self.backend_name = "GStreamer (subprocess)"
                return True
            # OpenCV in its place would hide that the backend the user asked for does not work.
            logger.error(
                "GStreamer could not open camera {}; it was asked for by name, so OpenCV is not tried",
                self.config.device_index,
            )
            self._capture = None
            return False

        self._capture = OpenCVCapture(self.config)
        if self._capture.open():
            self.backend_name = "OpenCV"
            return True

        return False

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Read a frame from the active backend."""
        if self._capture is None:
            return False, None
        return self._capture.read()

    def release(self) -> None:
        """Release the active backend."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def is_opened(self) -> bool:
        """Return True if the active backend is open."""
        return self._capture is not None and self._capture.is_opened()

    def failure_detail(self) -> str:
        """Why the backend delivers no frame, where it can tell; empty otherwise."""
        detail = getattr(self._capture, "failure_detail", None)
        return detail() if callable(detail) else ""

    def get_info(self) -> dict:
        """Return backend metadata for diagnostics."""
        if self._capture is not None:
            return self._capture.get_info()
        return {"backend": "None", "pipeline": "", "width": 0, "height": 0, "fps": 0}
