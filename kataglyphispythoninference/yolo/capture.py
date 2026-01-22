from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from kataglyphispythoninference.yolo.gstreamer import GStreamerSubprocessCapture
from kataglyphispythoninference.yolo.types import CameraConfig, CaptureBackend


class OpenCVCapture:
    """OpenCV video capture wrapper."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.cap: Optional[cv2.VideoCapture] = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    def open(self) -> bool:
        logger.info("Opening camera {} with OpenCV...", self.config.device_index)

        self.cap = cv2.VideoCapture(self.config.device_index)
        if not self.cap.isOpened():
            logger.error("Cannot open camera with OpenCV!")
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

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

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self.cap is None:
            return False, None
        return self.cap.read()

    def release(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None

    def is_opened(self) -> bool:
        return self.cap is not None and self.cap.isOpened()

    def get_info(self) -> dict:
        return {
            "backend": "OpenCV",
            "pipeline": f"OpenCV DirectShow (device {self.config.device_index})",
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
        }


class CameraCapture:
    """Unified camera capture supporting OpenCV and GStreamer subprocess backends."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._capture: Optional[object] = None
        self.backend_name = ""

    def open(self) -> bool:
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

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._capture is None:
            return False, None
        return self._capture.read()

    def release(self) -> None:
        if self._capture:
            self._capture.release()
            self._capture = None

    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.is_opened()

    def get_info(self) -> dict:
        if self._capture:
            return self._capture.get_info()
        return {"backend": "None", "pipeline": "", "width": 0, "height": 0, "fps": 0}
