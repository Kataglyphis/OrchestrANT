"""Camera capture backends for YOLO monitoring."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

import cv2
from loguru import logger

from kataglyphispythoninference.yolo.gstreamer import GStreamerSubprocessCapture
from kataglyphispythoninference.yolo.types import CaptureBackend


if TYPE_CHECKING:
    import numpy as np

    from kataglyphispythoninference.yolo.types import CameraConfig


class OpenCVCapture:
    """OpenCV video capture wrapper."""

    def __init__(self, config: CameraConfig) -> None:
        """Create an OpenCV capture instance."""
        self.config = config
        self.cap: cv2.VideoCapture | None = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    def open(self) -> bool:
        """Open the camera device and configure capture settings."""
        logger.info("Opening camera {} with OpenCV...", self.config.device_index)

        self.cap = cv2.VideoCapture(self.config.device_index)
        if not self.cap.isOpened():
            logger.error("Cannot open camera with OpenCV!")
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        with suppress(Exception):
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = float(self.cap.get(cv2.CAP_PROP_FPS))

        logger.success(
            "Camera opened: {}x{} @ {:.1f} FPS",
            self.actual_width,
            self.actual_height,
            self.actual_fps,
        )
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Read a frame from the camera."""
        if self.cap is None:
            return False, None
        return self.cap.read()

    def release(self) -> None:
        """Release the OpenCV capture handle."""
        if self.cap:
            self.cap.release()
            self.cap = None

    def is_opened(self) -> bool:
        """Return True if the camera is open."""
        return self.cap is not None and self.cap.isOpened()

    def get_info(self) -> dict:
        """Return backend metadata for diagnostics."""
        return {
            "backend": "OpenCV",
            "pipeline": f"OpenCV DirectShow (device {self.config.device_index})",
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }


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
        """Open the selected backend, falling back to OpenCV if needed."""
        if self.config.backend == CaptureBackend.GSTREAMER:
            logger.info("Using GStreamer subprocess capture...")
            self._capture = GStreamerSubprocessCapture(self.config)
            if self._capture.open():
                self.backend_name = "GStreamer (subprocess)"
                return True
            logger.warning("GStreamer failed, falling back to OpenCV...")

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

    def get_info(self) -> dict:
        """Return backend metadata for diagnostics."""
        if self._capture is not None:
            return self._capture.get_info()
        return {"backend": "None", "pipeline": "", "width": 0, "height": 0, "fps": 0}
