"""Helpers for streaming encoded frames."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import cv2
from loguru import logger


if TYPE_CHECKING:
    from collections.abc import Iterator

    from orchestr_ant_ion.streaming.capture import FrameCapture


# Created once at import; init_shutdown_event() re-arms it instead of
# rebinding, so no function needs a `global` statement.
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
    """Yield MJPEG frame chunks suitable for multipart responses.

    This generator will continue yielding frames until shutdown is requested
    via request_shutdown() or the frame capture stops.

    Args:
        frame_capture: The frame capture instance to read from.
        jpeg_quality: JPEG encoding quality (1-100).
        wait_for_frame: Time to wait for initial frame in seconds.
        wait_on_empty: Time to wait when no frame is available in seconds.

    Yields:
        MJPEG frame chunks ready for multipart responses.
    """
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
