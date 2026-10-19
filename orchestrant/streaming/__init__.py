"""The camera as a live web page: Picamera2 capture, served by Flet."""

from __future__ import annotations

from orchestrant.streaming.app import jpeg, run, stream_page
from orchestrant.streaming.capture import FrameCapture, initialize_camera


__all__ = [
    "FrameCapture",
    "initialize_camera",
    "jpeg",
    "run",
    "stream_page",
]
