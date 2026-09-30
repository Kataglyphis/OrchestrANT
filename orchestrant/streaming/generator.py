"""Helpers for streaming encoded frames."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import cv2
from loguru import logger


if TYPE_CHECKING:
    from collections.abc import Iterator

    from orchestrant.streaming.capture import FrameCapture


# Re-armed, never rebound, so no function needs a `global` statement.
_shutdown_event = threading.Event()


def init_shutdown_event() -> None:
    """Re-arm the shutdown event for a fresh streaming session."""
    _shutdown_event.clear()


def request_shutdown() -> None:
    """Request shutdown of all active frame generators."""
    _shutdown_event.set()


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested."""
    return _shutdown_event.is_set()


def gen_frames(
    frame_capture: FrameCapture,
    jpeg_quality: int = 30,
    wait_for_frame: float = 0.1,
    wait_on_empty: float = 0.5,
) -> Iterator[bytes]:
    """Yield MJPEG multipart chunks until shutdown is requested or capture stops."""
    logger.info("Starting video stream...")

    init_shutdown_event()

    while frame_capture.frame_queue.empty():
        if is_shutdown_requested():
            logger.info("Shutdown requested during initial frame wait")
            return
        logger.debug("Waiting for the first frame...")
        time.sleep(wait_for_frame)

    while not is_shutdown_requested():
        if is_shutdown_requested():
            logger.info("Shutdown requested, stopping stream")
            break

        frame = frame_capture.get_frame()
        if frame is None:
            logger.warning("No frame available; skipping frame.")
            time.sleep(wait_on_empty)
            continue

        ret, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if not ret:
            logger.warning("Frame encoding failed; skipping frame...")
            continue
        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n\r\n"
        )

    logger.info("Video stream stopped")
