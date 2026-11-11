"""Telling a dropped frame from a lost camera."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from orchestrant.pipeline.constants import (
    CAMERA_LOSS_TIMEOUT_SECONDS,
    CAMERA_RETRY_DELAY_SECONDS,
)


if TYPE_CHECKING:
    import numpy as np


class CameraLostError(RuntimeError):
    """Raised when the camera stops delivering frames."""


class _Camera(Protocol):
    def read(self) -> tuple[bool, np.ndarray | None]: ...

    def is_opened(self) -> bool: ...


def nominal_fps(camera_info: dict[str, object]) -> float:
    """The rate the camera reports for itself; 0 when the driver names none."""
    fps = camera_info.get("fps", 0)
    return float(fps) if isinstance(fps, int | float) and fps > 0 else 0.0


class FrameWatch:
    """Reads frames; a failed read is retried for a while, then the camera counts as lost."""

    def __init__(self, camera: _Camera) -> None:
        """Watch ``camera``; no read has failed yet."""
        self.camera = camera
        # When the reads began to fail; None while frames arrive.
        self.failing_since: float | None = None

    def read(self) -> np.ndarray | None:
        """One frame, or None for a failed read worth retrying; raises once the camera is gone."""
        ret, frame = self.camera.read()
        if ret and frame is not None:
            self.failing_since = None
            return frame
        now = time.perf_counter()
        if self.failing_since is None:
            self.failing_since = now
            logger.warning("Failed to grab frame")
        failing_for = now - self.failing_since
        if not self.camera.is_opened() or failing_for >= CAMERA_LOSS_TIMEOUT_SECONDS:
            msg = f"the camera delivered no frame for {failing_for:.1f} s"
            raise CameraLostError(msg)
        # A failed read returns at once; without the pause the loop holds a core and floods the log.
        time.sleep(CAMERA_RETRY_DELAY_SECONDS)
        return None
