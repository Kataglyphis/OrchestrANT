"""Camera capture utilities for Raspberry Pi Picamera2."""

from __future__ import annotations

import importlib
import threading
import time
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol, cast

import cv2
import numpy as np
from loguru import logger

from orchestrant.pipeline.latest import LatestSlot


if TYPE_CHECKING:
    from types import ModuleType


@lru_cache(maxsize=1)
def _get_picamera2() -> ModuleType:
    """Import and cache the Picamera2 module."""
    return importlib.import_module("picamera2")


def initialize_camera() -> object:
    """Initialize and start the Picamera2 device."""
    picamera2 = _get_picamera2()
    camera = picamera2.Picamera2()
    camera.configure("preview")
    camera.start()
    logger.info("Camera initialized successfully.")
    return camera


class _CameraProtocol(Protocol):
    """Protocol for camera objects used by this module."""

    def capture_array(self) -> np.ndarray | None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


# After a failed restart: retrying at the capture interval would log an error thirty times a second.
RESTART_PAUSE_S = 1.0


class FrameCapture:
    """Background frame capture using Picamera2; every reader sees the newest frame."""

    def __init__(self, capture_interval: float = 0.03) -> None:
        """Create a frame capture worker.

        Args:
            capture_interval: Sleep between captures in seconds.
        """
        self.camera: _CameraProtocol = cast("_CameraProtocol", initialize_camera())
        self.capture_interval = capture_interval
        self._frames: LatestSlot[np.ndarray] = LatestSlot()
        self.running = True
        self._camera_lock = threading.Lock()
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self) -> None:
        """Continuously capture frames into the queue."""
        while self.running:
            try:
                with self._camera_lock:
                    if not self.running:
                        break
                    frame = self.camera.capture_array()

                if frame is None or frame.size == 0:
                    logger.warning("Received empty frame, retrying...")
                    continue

                logger.debug("Frame captured successfully with size {}", frame.shape)
                frame = cv2.rotate(frame, cv2.ROTATE_180)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                self._publish(frame)
            except (OSError, RuntimeError) as exc:
                logger.error("Error during frame capture: {}", exc)
                if not self.restart_camera():
                    time.sleep(RESTART_PAUSE_S)
            time.sleep(self.capture_interval)

    def _publish(self, frame: np.ndarray) -> None:
        self._frames.publish(frame)

    def next_frame(self, seen: int, timeout: float) -> tuple[int, np.ndarray | None]:
        """Wait up to ``timeout`` seconds for a frame newer than number ``seen``.

        Returns the newest frame with its number, or ``(seen, None)`` when none came.
        """
        return self._frames.next(seen, timeout)

    def get_frame(self) -> np.ndarray | None:
        """Return the latest frame, or None before the first one."""
        return self._frames.latest()

    def stop(self) -> None:
        """Stop the capture thread and let go of the camera."""
        self.running = False
        self.thread.join()
        self._release_camera()

    def _release_camera(self) -> None:
        # A camera that already failed may refuse to stop; the device is released either way.
        with suppress(OSError, RuntimeError):
            self.camera.stop()
        with suppress(OSError, RuntimeError):
            self.camera.close()

    def restart_camera(self) -> bool:
        """Restart the camera after a capture failure; False when it would not start."""
        logger.info("Restarting camera...")
        with self._camera_lock:
            # The old device lets go first, or the new Picamera2 finds the sensor busy.
            self._release_camera()
            try:
                self.camera = cast("_CameraProtocol", initialize_camera())
            except (OSError, RuntimeError) as exc:
                logger.error("Camera restart failed: {}", exc)
                return False
        return True

    @staticmethod
    def get_fallback_frame() -> np.ndarray:
        """Return a fallback black frame when no data is available."""
        return np.zeros((480, 640, 3), dtype=np.uint8)
