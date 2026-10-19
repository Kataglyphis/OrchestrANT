"""The camera stream page, driven without a camera or a Flet client."""

from __future__ import annotations

import asyncio
import threading
import types
from typing import TYPE_CHECKING

import numpy as np
import pytest


pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("flet", reason="the frontend extra")

from orchestrant.streaming import app


if TYPE_CHECKING:
    from collections.abc import Iterator

FRAME = np.zeros((48, 64, 3), dtype=np.uint8)


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop on its own thread, standing in for Flet's."""
    running = asyncio.new_event_loop()
    thread = threading.Thread(target=running.run_forever, daemon=True)
    thread.start()
    yield running
    running.call_soon_threadsafe(running.stop)
    thread.join(5)
    running.close()


class Capture:
    """Hands out `frames` frames, then None, as an idle Picamera2 queue does."""

    def __init__(self, frames: int) -> None:
        """Queue up `frames` black frames."""
        self.left = frames
        self.stopped = False

    def get_frame(self) -> np.ndarray | None:
        """The next frame, or None once they ran out."""
        if self.left == 0:
            return None
        self.left -= 1
        return FRAME

    def stop(self) -> None:
        """Record that the server's exit stopped the capture."""
        self.stopped = True


def test_a_frame_becomes_jpeg() -> None:
    """The stream sends JPEG, as the MJPEG page did."""
    data = app.jpeg(FRAME)
    assert data is not None
    assert data[:2] == b"\xff\xd8"


def test_the_pump_draws_every_frame_until_the_tab_closes(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Each captured frame is drawn once; the tab closing ends the pump."""
    drawn: list[bytes] = []
    stop = threading.Event()

    async def encoded(data: bytes) -> None:
        drawn.append(data[:2])
        if len(drawn) == 3:
            stop.set()

    image = types.SimpleNamespace(render_encoded=encoded)
    app.pump(loop, image, Capture(3), stop)
    assert drawn == [b"\xff\xd8"] * 3


def test_a_tab_that_is_gone_ends_its_pump(loop: asyncio.AbstractEventLoop) -> None:
    """A failed draw stops this tab's pump instead of spinning on it."""
    stop = threading.Event()

    async def gone(_data: bytes) -> None:
        message = "session closed"
        raise RuntimeError(message)

    app.pump(loop, types.SimpleNamespace(render_encoded=gone), Capture(5), stop)
    assert stop.is_set()


def test_each_tab_gets_its_own_image_and_pump() -> None:
    """A browser tab gets a RawImage and a pump thread of its own."""
    added: list[object] = []
    threads: list[tuple] = []
    page = types.SimpleNamespace(
        loop=object(),
        add=lambda *controls: added.extend(controls),
        run_thread=lambda fn, *args: threads.append((fn, args)),
    )
    app.stream_page(Capture(0))(page)
    assert page.title == "Live Streaming"
    assert len(threads) == 1
    assert threads[0][0] is app.pump
    assert any(type(c).__name__ == "Container" for c in added)


def test_run_serves_on_the_configured_host_and_stops_the_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$KATAGLYPHIS_STREAM_HOST binds the server; leaving it stops the camera."""
    capture = Capture(0)
    served: list[tuple[str, int]] = []

    def server(_main: object, host: str, port: int) -> object:
        served.append((host, port))
        return types.SimpleNamespace(run=lambda: None)

    monkeypatch.setenv(app.HOST_ENV, "0.0.0.0")  # noqa: S104
    monkeypatch.setattr(app, "setup_logging", lambda: None)
    monkeypatch.setattr(app, "FrameCapture", lambda: capture)
    monkeypatch.setattr(app, "web_server", server)
    app.run()
    assert served == [("0.0.0.0", app.PORT)]  # noqa: S104
    assert capture.stopped
