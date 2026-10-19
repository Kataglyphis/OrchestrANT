"""The Flet viewer, driven without a Flet client: frames, labels, trends and shutdown."""

# ruff: noqa: SLF001 -- a session's controls are private; the tests read them back

from __future__ import annotations

import asyncio
import threading
import time
import types
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest


pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("flet", reason="the frontend extra")

from orchestrant.pipeline.types import PerformanceMetrics, SystemStats
from orchestrant.pipeline.ui import flet_viewer as fv


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


def session(loop: asyncio.AbstractEventLoop, *, web: bool = False) -> Any:  # noqa: ANN401
    """A session on a fake page whose RawImage records what it was handed."""
    page = types.SimpleNamespace(web=web, loop=loop, update=lambda: None)
    built = fv._Session(page)
    built.drawn = []

    async def rgba(width: int, height: int, pixels: bytes) -> None:
        built.drawn.append(("rgba", width, height, len(pixels)))

    async def encoded(data: bytes) -> None:
        built.drawn.append(("jpeg", data[:2]))

    built.image = types.SimpleNamespace(render_rgba=rgba, render_encoded=encoded)
    return built


def feed(viewer: fv.FletViewer, target: Any, frames: int) -> threading.Thread:  # noqa: ANN401
    """Pump `target` on a thread while the test renders `frames` frames."""
    pump = threading.Thread(target=viewer.pump, args=(target,))
    pump.start()
    stats = SystemStats(cpu_percent=40.0, ram_percent=50.0, gpu_name="N/A")
    for i in range(frames):
        viewer.render(
            FRAME,
            perf_metrics=PerformanceMetrics(camera_fps=30.0, inference_ms=10.0 + i),
            sys_stats=stats,
            proc_stats={"cpu_percent": 5.0, "memory_mb": 100.0, "threads": 4},
            camera_info={"backend": "opencv"},
            detections_count=i,
            log_lines=[f"line {i}"],
        )
        time.sleep(0.02)
    return pump


def test_a_trend_spans_the_chart_and_clamps_to_it() -> None:
    """Oldest at x=0, newest at the right edge; values above `top` sit on the top edge."""
    width, height = fv.CHART_SIZE
    points = fv.polyline([0.0, 50.0, 200.0], 100.0)
    assert points == [(0.0, height), (width / 2, height / 2), (width, 0.0)]
    assert fv.polyline([], 100.0) == []


def test_the_history_keeps_the_last_samples_and_reads_no_gpu_as_zero() -> None:
    """Each series is bounded; a frame without both readings adds nothing."""
    history = fv.TrendHistory(maxlen=3)
    for fps in range(5):
        history.add(PerformanceMetrics(camera_fps=fps), SystemStats(gpu_name="N/A"))
    history.add(None, SystemStats())
    trends = history.copy()
    assert trends["camera_fps"] == [2.0, 3.0, 4.0]
    assert trends["gpu"] == [0.0, 0.0, 0.0]


def test_a_window_gets_raw_rgba_and_its_labels(loop: asyncio.AbstractEventLoop) -> None:
    """The newest frames reach the window as RGBA, and the labels follow them."""
    viewer = fv.FletViewer(64, 48)
    window = session(loop)
    pump = feed(viewer, window, 5)
    viewer.close()
    pump.join(5)
    assert not pump.is_alive()
    assert window.drawn
    assert window.drawn[-1] == ("rgba", 64, 48, 64 * 48 * 4)
    time.sleep(0.1)  # the last label update is queued on the loop
    assert window.labels["camera_fps"].value == "Camera Input: 30.0 FPS"
    assert window.labels["camera_fps"].visible
    assert not window.labels["gpu"].visible  # no GPU reading, so no blank line
    assert window.log.value.startswith("line ")
    assert window.lines["camera_fps"].points


def test_a_browser_tab_gets_jpeg(loop: asyncio.AbstractEventLoop) -> None:
    """A web session is sent JPEG, not raw pixels."""
    viewer = fv.FletViewer(64, 48, web=True)
    tab = session(loop, web=True)
    pump = feed(viewer, tab, 3)
    viewer.close()
    pump.join(5)
    assert tab.drawn
    assert {kind for kind, *_ in tab.drawn} == {"jpeg"}
    assert tab.drawn[0][1] == b"\xff\xd8"


def test_a_failing_session_ends_its_pump_not_the_viewer(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """A window that is gone raises in its pump; the viewer stays open for the others."""
    viewer = fv.FletViewer(64, 48)
    gone = session(loop)

    async def closed(*_args: object) -> None:
        message = "window closed"
        raise RuntimeError(message)

    gone.image = types.SimpleNamespace(render_rgba=closed)
    pump = feed(viewer, gone, 2)
    pump.join(5)
    assert not pump.is_alive()
    assert gone.closed.is_set()
    assert viewer.is_open()


def test_closing_the_window_closes_the_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the desktop app returns, the detection loop is told to stop."""
    calls = []
    monkeypatch.setattr(fv.ft, "run", lambda _main, **kw: calls.append(kw))
    viewer = fv.FletViewer(64, 48)
    viewer.run()
    assert calls == [{"view": fv.ft.AppView.FLET_APP}]
    assert not viewer.is_open()


def test_a_client_that_never_opened_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop client that exits before any window opened is reported, not silent."""
    errors: list[str] = []
    monkeypatch.setattr(fv.ft, "run", lambda _main, **_kw: None)
    monkeypatch.setattr(fv.logger, "error", lambda message, *_a: errors.append(message))
    fv.FletViewer(64, 48).run()
    assert len(errors) == 1
    assert "--ui opencv" in errors[0]


def test_close_stops_the_web_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() from the detection thread ends a server running on the main thread."""
    viewer = fv.FletViewer(64, 48, web=True, host="0.0.0.0", port=9000)  # noqa: S104
    server = types.SimpleNamespace(should_exit=False)
    server.run = viewer.close
    seen = []

    def fake(_main: object, host: str, port: int) -> object:
        seen.append((host, port))
        return server

    monkeypatch.setattr(fv, "web_server", fake)
    viewer.run()
    assert seen == [("0.0.0.0", 9000)]  # noqa: S104
    assert server.should_exit is True
    assert not viewer.is_open()


def test_a_viewer_closed_before_it_runs_never_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection that ends at once must not leave a server running forever."""
    viewer = fv.FletViewer(64, 48, web=True)
    viewer.close()
    served = []
    server = types.SimpleNamespace(should_exit=False, run=lambda: served.append(1))
    monkeypatch.setattr(fv, "web_server", lambda *_a: server)
    viewer.run()
    assert served == []


def test_a_render_after_close_is_dropped() -> None:
    """The detection loop may render once more after the window closed."""
    viewer = fv.FletViewer(64, 48)
    viewer.close()
    viewer.render(FRAME)
    assert viewer._snapshot is None
