"""Helpers for streaming encoded frames."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import cv2
from loguru import logger


if TYPE_CHECKING:
    from collections.abc import Iterator

    from orchestr_ant_ion.streaming.capture import FrameCapture


def gen_frames(
    frame_capture: FrameCapture,
    jpeg_quality: int = 30,
    wait_for_frame: float = 0.1,
    wait_on_empty: float = 0.5,
) -> Iterator[bytes]:
    """Yield MJPEG frame chunks suitable for multipart responses."""
    logger.info("Starting video stream...")
    while frame_capture.get_frame() is None:
        logger.debug("Waiting for the first frame...")
        time.sleep(wait_for_frame)

    while True:
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

