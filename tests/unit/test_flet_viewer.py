"""The Flet viewer, driven without a Flet client: frames, labels, trends and shutdown."""

# ruff: noqa: SLF001 -- a session's controls are private; the tests read them back

from __future__ import annotations

import ast
import asyncio
import threading
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest


pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("flet", reason="the frontend extra")

import cv2

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


def settled(loop: asyncio.AbstractEventLoop) -> None:
    """Wait until the loop has run every callback queued before this call."""
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)


def session(loop: asyncio.AbstractEventLoop, *, web: bool = False) -> Any:  # noqa: ANN401
    """A session on a fake page whose RawImage records what it was handed and signals each draw."""
    page = types.SimpleNamespace(web=web, loop=loop, update=lambda: None)
    built = fv._Session(page)
    built.drawn = []
    built.each_draw = threading.Semaphore(0)

    async def rgba(width: int, height: int, pixels: bytes) -> None:
        built.drawn.append(("rgba", width, height, len(pixels)))
        built.each_draw.release()

    async def encoded(data: bytes) -> None:
        built.drawn.append(("jpeg", data[:2]))
        built.each_draw.release()

    built.image = types.SimpleNamespace(render_rgba=rgba, render_encoded=encoded)
    return built


def feed(
    viewer: fv.FletViewer,
    target: Any,  # noqa: ANN401
    frames: int,
    *,
    drawn: bool = True,
) -> threading.Thread:
    """Pump `target` on a thread while the test renders `frames` frames, each drawn before the next."""
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
        if drawn:
            assert target.each_draw.acquire(timeout=5), f"frame {i} was never drawn"
    return pump


def test_a_trend_spans_the_chart_and_clamps_to_it() -> None:
    """Oldest at x=0, newest at the right edge; values above `top` sit on the top edge."""
    width, height = fv.CHART_SIZE
    points = fv.polyline([0.0, 50.0, 200.0], 100.0)
    assert points == [(0.0, height), (width / 2, height / 2), (width, 0.0)]
    assert fv.polyline([], 100.0) == []


def test_a_large_frame_is_sent_scaled_down_and_a_small_one_as_it_is() -> None:
    """A 1080p frame reaches a window as 1280x720 RGBA and a tab as 1280x720 JPEG."""
    big = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert fv.frame_payload(big, web=False)[:2] == (1280, 720)
    sent = fv.frame_payload(big, web=True)
    assert isinstance(sent, bytes)
    decoded = cv2.imdecode(np.frombuffer(sent, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (720, 1280)
    assert fv.fitted(FRAME) is FRAME


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
    settled(loop)  # the last label update is queued on the loop
    assert window.labels["camera_fps"].value == "Camera Input: 30.0 FPS"
    assert window.labels["camera_fps"].visible
    assert not window.labels["gpu"].visible  # no GPU reading, so no blank line
    assert window.log.value.startswith("line ")
    assert window.lines["camera_fps"].points


@pytest.mark.parametrize(("interval", "every_frame"), [(60.0, False), (0.0, True)])
def test_the_labels_and_trends_are_drawn_at_their_own_rate_not_per_frame(
    loop: asyncio.AbstractEventLoop,
    monkeypatch: pytest.MonkeyPatch,
    interval: float,
    every_frame: bool,  # noqa: FBT001 -- a parametrized case
) -> None:
    """Every frame is drawn; the page update with its labels and five trend lines comes once per interval."""
    monkeypatch.setattr(fv, "TELEMETRY_INTERVAL_S", interval)
    viewer = fv.FletViewer(64, 48)
    window = session(loop)
    shown = []
    window.show = shown.append
    pump = feed(viewer, window, 6)
    viewer.close()
    pump.join(5)
    settled(loop)  # the last update is queued on the loop
    assert len(window.drawn) >= 2
    assert len(shown) == (len(window.drawn) if every_frame else 1)


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
    pump = feed(viewer, gone, 2, drawn=False)
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


def test_a_closed_session_is_forgotten(loop: asyncio.AbstractEventLoop) -> None:
    """A tab that went away leaves the viewer's list, so reconnects do not pile up pages."""
    viewer = fv.FletViewer(64, 48, web=True)
    tab = session(loop, web=True)
    viewer._sessions.append(tab)
    pump = feed(viewer, tab, 1)
    tab.closed.set()
    pump.join(5)
    assert not pump.is_alive()
    assert viewer._sessions == []
    assert viewer.is_open()


def test_the_history_grows_by_one_per_refresh_not_per_frame() -> None:
    """The monitor hands the same readings for ten frames; only new ones are a new sample."""
    history = fv.TrendHistory()
    perf, stats = PerformanceMetrics(camera_fps=30.0), SystemStats(gpu_name="N/A")
    for _ in range(10):
        history.add(perf, stats)
    history.add(PerformanceMetrics(camera_fps=29.0), stats)
    assert history.copy()["camera_fps"] == [30.0, 29.0]


def test_a_window_that_opened_and_closed_reports_no_missing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The never-opened error is for a client that died first, not for every closed window."""
    errors: list[str] = []
    page = types.SimpleNamespace(
        web=False,
        window=types.SimpleNamespace(),
        add=lambda _control: None,
        run_thread=lambda _target, _session: None,
    )
    monkeypatch.setattr(fv.ft, "run", lambda main, **_kw: main(page))
    monkeypatch.setattr(fv.logger, "error", lambda message, *_a: errors.append(message))
    viewer = fv.FletViewer(64, 48)
    viewer.run()
    assert errors == []


def test_no_test_here_waits_with_a_real_sleep() -> None:
    """A fixed sleep fails on a loaded Pi and wastes time elsewhere; the tests wait on a draw or the loop."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    sleeps = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "sleep"
        and isinstance(node.value, ast.Name)
        and node.value.id == "time"
    ]
    assert sleeps == []
