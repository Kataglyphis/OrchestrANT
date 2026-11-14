"""OpenCV capture backend."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import cv2
from loguru import logger


if TYPE_CHECKING:
    import numpy as np

    from orchestrant.pipeline.types import CameraConfig


def fourcc_text(code: float) -> str:
    """The four characters of a FOURCC as CAP_PROP_FOURCC reports it, or "" when the driver names none."""
    value = int(code)
    text = "".join(chr((value >> (8 * shift)) & 0xFF) for shift in range(4))
    return text if value and text.isprintable() else ""


class OpenCVCapture:
    """OpenCV video capture wrapper."""

    def __init__(self, config: CameraConfig) -> None:
        """Create an OpenCV capture instance."""
        self.config = config
        self.cap: cv2.VideoCapture | None = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0
        # What the driver agreed to: many USB cameras give 1080p30 only as MJPG, and raw YUYV at a few frames.
        self.fourcc = ""
        self.backend_name = "OpenCV"

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
        self.fourcc = fourcc_text(self.cap.get(cv2.CAP_PROP_FOURCC))
        # getBackendName raises on a build without the call; the backend then stays unnamed rather than guessed.
        with suppress(Exception):
            self.backend_name = f"OpenCV {self.cap.getBackendName()}"

        logger.success(
            "Camera opened: {}x{} @ {:.1f} FPS as {} through {}",
            self.actual_width,
            self.actual_height,
            self.actual_fps,
            self.fourcc or "an unnamed format",
            self.backend_name,
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
            "pipeline": f"{self.backend_name} (device {self.config.device_index})",
            "fourcc": self.fourcc,
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }
