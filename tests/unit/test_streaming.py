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

from orchestrant.streaming import app, capture as stream_capture


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
    """Hands out `frames` frames, then None, as an idle Picamera2 does."""

    def __init__(self, frames: int) -> None:
        """Queue up `frames` black frames."""
        self.left = frames
        self.stopped = False

    def next_frame(self, seen: int, _timeout: float) -> tuple[int, np.ndarray | None]:
        """The next frame with its number, or None once they ran out."""
        if self.left == 0:
            return seen, None
        self.left -= 1
        return seen + 1, FRAME

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


class Camera:
    """A Picamera2 stand-in: numbered frames, and the calls made on it."""

    def __init__(self) -> None:
        """Start with no frame taken and nothing called."""
        self.taken = 0
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def capture_array(self) -> np.ndarray:
        """The next frame, its number in every pixel; or the failure a test planted."""
        if self.fail is not None:
            raise self.fail
        self.taken += 1
        return np.full((2, 2, 3), self.taken % 256, dtype=np.uint8)

    def stop(self) -> None:
        """Record the stop."""
        self.calls.append("stop")

    def close(self) -> None:
        """Record the close."""
        self.calls.append("close")


@pytest.fixture
def cameras(monkeypatch: pytest.MonkeyPatch) -> list[Camera]:
    """Every camera `initialize_camera` hands out, in order."""
    made: list[Camera] = []

    def initialize() -> Camera:
        made.append(Camera())
        return made[-1]

    monkeypatch.setattr(stream_capture, "initialize_camera", initialize)
    return made


def test_every_tab_sees_the_newest_frame_and_a_slow_one_skips(
    cameras: list[Camera],
) -> None:
    """A queue gave each frame to one tab and the oldest first; the slot gives all of them the newest."""
    capture = stream_capture.FrameCapture(capture_interval=0.001)
    try:
        first_seen, first = capture.next_frame(0, 5.0)
        assert first is not None
        # Two readers at the same number wait for the same next frame.
        a_seen, a = capture.next_frame(first_seen, 5.0)
        b_seen, b = capture.next_frame(first_seen, 5.0)
        assert a is not None
        assert b is not None
        assert a_seen > first_seen
        assert b_seen >= a_seen
        # A reader that slept gets the newest frame, not the next in line.
        while capture.next_frame(a_seen, 5.0)[0] < a_seen + 20:
            pass
        late_seen, late = capture.next_frame(a_seen, 5.0)
        assert late is not None
        assert late_seen >= a_seen + 20
        assert capture.get_frame() is not None
    finally:
        capture.stop()
    assert cameras[0].calls == ["stop", "close"]
    # Nothing new arrives after the stop, so a reader's wait runs out.
    seen, _frame = capture.next_frame(0, 0.0)
    assert capture.next_frame(seen, 0.01) == (seen, None)


def test_a_failed_capture_restarts_the_camera_and_the_thread_survives_a_failed_restart(
    cameras: list[Camera], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old device is released before the new one starts; a restart that fails is tried again."""
    monkeypatch.setattr(stream_capture, "RESTART_PAUSE_S", 0.001)
    capture = stream_capture.FrameCapture(capture_interval=0.001)
    try:
        seen, _frame = capture.next_frame(0, 5.0)
        made = len(cameras)
        initialize = stream_capture.initialize_camera
        failures = [RuntimeError("sensor busy")]

        def flaky() -> object:
            if failures:
                raise failures.pop()
            return initialize()

        monkeypatch.setattr(stream_capture, "initialize_camera", flaky)
        cameras[0].fail = RuntimeError("capture timed out")
        # The second attempt succeeds, and frames flow again from the new camera.
        seen, frame = capture.next_frame(seen, 5.0)
        while len(cameras) == made:
            seen, frame = capture.next_frame(seen, 5.0)
        assert frame is not None
        assert capture.thread.is_alive()
        assert cameras[0].calls[:2] == ["stop", "close"]
        assert cameras[-1].taken > 0
    finally:
        capture.stop()
