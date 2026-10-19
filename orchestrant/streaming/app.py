"""The camera as a live Flet web page: ``python -m orchestrant.streaming``."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Any

import cv2
from loguru import logger

from orchestrant.logging_config import setup_logging
from orchestrant.pipeline.ui.flet_viewer import require_flet, web_server
from orchestrant.streaming.capture import FrameCapture


if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np


HOST_ENV = "KATAGLYPHIS_STREAM_HOST"
DEFAULT_HOST = "127.0.0.1"
PORT = 5000
JPEG_QUALITY = 30
FRAME_TIMEOUT_S = 2.0
WAIT_ON_EMPTY_S = 0.5


def jpeg(frame: np.ndarray, quality: int = JPEG_QUALITY) -> bytes | None:
    """One frame as JPEG bytes; None when encoding fails."""
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buffer.tobytes() if ok else None


def pump(
    loop: asyncio.AbstractEventLoop,
    image: Any,  # noqa: ANN401 - a flet.RawImage
    capture: FrameCapture,
    stop: threading.Event,
) -> None:
    """Draw the capture's frames into `image` until the tab closes."""
    while not stop.is_set():
        frame = capture.get_frame()
        if frame is None:
            stop.wait(WAIT_ON_EMPTY_S)
            continue
        data = jpeg(frame)
        if data is None:
            logger.warning("Frame encoding failed; skipping frame")
            continue
        future = asyncio.run_coroutine_threadsafe(image.render_encoded(data), loop)
        try:
            future.result(timeout=FRAME_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
        except Exception as exc:  # the tab is gone; its pump ends, the server stays
            logger.debug("Stream session ended: {}", exc)
            stop.set()


def stream_page(capture: FrameCapture) -> Callable[[Any], None]:
    """The page each browser tab gets: a title and the live frame, fed by its own pump."""
    flet = require_flet()

    def main(page: Any) -> None:  # noqa: ANN401 - a flet.Page
        page.title = "Live Streaming"
        page.theme = flet.Theme(color_scheme_seed=flet.Colors.GREEN_ACCENT)
        image = flet.RawImage(expand=True, fit=flet.BoxFit.CONTAIN)
        stop = threading.Event()
        page.on_close = page.on_disconnect = lambda _event: stop.set()
        page.add(
            flet.Text("Live Streaming", size=24, weight=flet.FontWeight.BOLD),
            flet.Container(content=image, expand=True),
        )
        page.run_thread(pump, page.loop, image, capture, stop)

    return main


def run() -> None:
    """Serve the Picamera2 stream on port 5000 until Ctrl+C; $KATAGLYPHIS_STREAM_HOST binds it."""
    setup_logging()
    host = os.getenv(HOST_ENV, DEFAULT_HOST)
    capture = FrameCapture()
    try:
        logger.info("Streaming at http://{}:{}", host, PORT)
        web_server(stream_page(capture), host, PORT).run()
    finally:
        capture.stop()


if __name__ == "__main__":
    run()
