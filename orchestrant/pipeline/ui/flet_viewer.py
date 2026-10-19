"""Flet viewer for monitoring pipelines: a desktop window, or with ``web`` a page browsers open.

The detection thread calls :meth:`FletViewer.render`; each window or browser tab has a
pump thread that pushes the newest frame and drops the ones it could not keep up with.
Controls change only on Flet's event loop, which every session shares.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cv2
from loguru import logger

from orchestrant.pipeline.ui.viewmodel import format_labels


ft: Any
cv: Any
uvicorn: Any
_FLET_IMPORT_ERROR: ImportError | None = None
try:
    import flet as _ft  # ty: ignore[unresolved-import]
    import flet.canvas as _cv  # ty: ignore[unresolved-import]
except ImportError as exc:  # pragma: no cover - optional dependency
    ft = cv = None
    _FLET_IMPORT_ERROR = exc
else:
    ft, cv = _ft, _cv
try:
    import uvicorn as _uvicorn  # ty: ignore[unresolved-import]
except (
    ImportError
):  # pragma: no cover - flet-web brings it, and the extra leaves it out on Windows
    uvicorn = None
else:
    uvicorn = _uvicorn

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from orchestrant.pipeline.types import (
        PerformanceMetrics,
        SystemStats,
    )
    from orchestrant.pipeline.ui.viewmodel import ViewLabels


FLET_AVAILABLE = ft is not None
HISTORY = 120
SIDE_WIDTH = 380
CHART_SIZE = (340.0, 110.0)
FRAME_TIMEOUT_S = 2.0
JPEG_QUALITY = 70
# The frame area is at most ~1220 px wide; a 1080p frame as RGBA is 250 MB/s at 30 fps, 720p 110.
MAX_EDGE = 1280

# The panels and the ViewLabels fields each shows, as the wxPython and DearPyGui viewers had them.
SECTIONS = (
    (
        "Overview",
        (
            "resolution",
            "capture",
            "backend",
            "pipeline",
            "cpu_model",
            "ram_total",
            "gpu_model",
            "vram_total",
            "detections",
        ),
    ),
    ("Performance", ("camera_fps", "inference_ms", "budget", "headroom")),
    ("System", ("sys_cpu", "sys_ram", "gpu", "vram", "power", "energy")),
    ("Process", ("proc", "classification")),
)

# (title, series, legend, fixed top or None to fit the data)
TRENDS = (
    (
        "Performance",
        ("camera_fps", "inference_ms"),
        ("Camera FPS", "Inference ms"),
        None,
    ),
    ("System", ("cpu", "ram", "gpu"), ("CPU %", "RAM %", "GPU %"), 100.0),
)
SERIES_COLORS = ("PRIMARY", "TERTIARY", "ORANGE")


def require_flet() -> Any:  # noqa: ANN401 - the flet module, or ImportError
    """The flet module; ImportError naming the extra when it is not installed."""
    if ft is None:
        message = "the Flet viewer needs flet (the frontend extra)"
        raise ImportError(message) from _FLET_IMPORT_ERROR
    return ft


def web_server(main: Callable[[Any], None], host: str, port: int) -> Any:  # noqa: ANN401
    """A uvicorn server for `main` as a Flet web app; ``should_exit = True`` stops it from any thread."""
    flet = require_flet()
    if uvicorn is None:
        message = (
            "serving to a browser needs flet-web (the frontend extra, off Windows)"
        )
        raise ImportError(message)
    app = flet.run(main, export_asgi_app=True)
    return uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )


def polyline(values: list[float], top: float) -> list[tuple[float, float]]:
    """Canvas points for one series, oldest at the left and `top` at the top edge."""
    width, height = CHART_SIZE
    top = top or 1.0
    step = width / max(len(values) - 1, 1)
    return [
        (i * step, height - min(max(v / top, 0.0), 1.0) * height)
        for i, v in enumerate(values)
    ]


def fitted(frame: np.ndarray, max_edge: int = MAX_EDGE) -> np.ndarray:
    """The frame scaled down to a long edge of at most `max_edge`; a smaller one as it is."""
    height, width = frame.shape[:2]
    scale = max_edge / max(height, width)
    if scale >= 1:
        return frame
    size = (round(width * scale), round(height * scale))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def frame_payload(
    frame: np.ndarray, *, web: bool
) -> tuple[int, int, bytes] | bytes | None:
    """What a session's RawImage takes, at most MAX_EDGE wide: JPEG for a browser, RGBA for a window."""
    frame = fitted(frame)
    if web:
        ok, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        return buffer.tobytes() if ok else None
    rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
    height, width = rgba.shape[:2]
    return width, height, rgba.tobytes()


class TrendHistory:
    """The last HISTORY samples per series."""

    def __init__(self, maxlen: int = HISTORY) -> None:
        """Start every series empty."""
        names = [name for _, series, _, _ in TRENDS for name in series]
        self.series: dict[str, deque[float]] = {n: deque(maxlen=maxlen) for n in names}

    def add(self, perf: PerformanceMetrics | None, stats: SystemStats | None) -> None:
        """Append one sample per series; a frame without both readings adds nothing."""
        if perf is None or stats is None:
            return
        self.series["camera_fps"].append(float(perf.camera_fps))
        self.series["inference_ms"].append(float(perf.inference_ms))
        self.series["cpu"].append(float(stats.cpu_percent))
        self.series["ram"].append(float(stats.ram_percent))
        gpu = float(stats.gpu_percent) if stats.gpu_name != "N/A" else 0.0
        self.series["gpu"].append(gpu)

    def copy(self) -> dict[str, list[float]]:
        """Each series as a list, safe to hand to another thread."""
        return {name: list(values) for name, values in self.series.items()}


@dataclass(slots=True)
class Snapshot:
    """One render() call, formatted once for every session to draw."""

    frame: np.ndarray
    labels: ViewLabels
    log: str
    trends: dict[str, list[float]]


class _Session:
    """One window or browser tab: its controls, drawn on the event loop."""

    def __init__(self, page: Any) -> None:  # noqa: ANN401 - a flet.Page
        flet = require_flet()
        self.page = page
        self.web = bool(page.web)
        self.closed = threading.Event()
        self.image = flet.RawImage(expand=True, fit=flet.BoxFit.CONTAIN)
        self.labels = {
            key: flet.Text("", size=12, selectable=True, visible=False)
            for _, keys in SECTIONS
            for key in keys
        }
        self.log = flet.Text("", size=11, font_family="monospace", selectable=True)
        self.lines = {
            name: cv.Points(
                point_mode=cv.PointMode.POLYGON,
                paint=flet.Paint(
                    color=getattr(flet.Colors, SERIES_COLORS[i]),
                    stroke_width=2,
                    style=flet.PaintingStyle.STROKE,
                ),
            )
            for _, series, _, _ in TRENDS
            for i, name in enumerate(series)
        }

    def layout(self, title: str) -> Any:  # noqa: ANN401 - a flet control
        """The frame on the left, the panels, trends and log on the right."""
        flet = require_flet()
        side = [flet.Text(title, size=18, weight=flet.FontWeight.BOLD)]
        for heading, keys in SECTIONS:
            side.append(_panel(heading, [self.labels[k] for k in keys]))
        side += [_trend_panel(t, self.lines) for t in TRENDS]
        side.append(_panel("Logs", [self.log]))
        return flet.Row(
            [
                flet.Container(content=self.image, expand=True),
                flet.Container(
                    content=flet.Column(
                        side,
                        scroll=flet.ScrollMode.AUTO,
                        spacing=8,
                        horizontal_alignment=flet.CrossAxisAlignment.STRETCH,
                    ),
                    width=SIDE_WIDTH,
                ),
            ],
            expand=True,
            vertical_alignment=flet.CrossAxisAlignment.START,
        )

    def show(self, snapshot: Snapshot) -> None:
        """Set every label and trend line from one snapshot; runs on the event loop."""
        try:
            for key, text in self.labels.items():
                text.value = getattr(snapshot.labels, key)
                text.visible = bool(text.value)
            self.log.value = snapshot.log
            for _, series, _, top in TRENDS:
                fitted = top or max(
                    (max(snapshot.trends[n], default=0.0) for n in series), default=1.0
                )
                for name in series:
                    self.lines[name].points = polyline(snapshot.trends[name], fitted)
            self.page.update()
        except (
            Exception
        ) as exc:  # a window closing under an update ends this session only
            logger.debug("Flet session update failed: {}", exc)
            self.closed.set()


def _panel(heading: str, rows: list[Any]) -> Any:  # noqa: ANN401 - a flet control
    flet = require_flet()
    return flet.Container(
        content=flet.Column(
            [flet.Text(heading, size=13, color=flet.Colors.ON_SURFACE_VARIANT), *rows],
            spacing=2,
        ),
        padding=flet.Padding.all(10),
        border=flet.Border.all(1, flet.Colors.OUTLINE_VARIANT),
        border_radius=flet.BorderRadius.all(8),
    )


def _trend_panel(trend: tuple, lines: dict[str, Any]) -> Any:  # noqa: ANN401 - a flet control
    flet = require_flet()
    heading, series, legend, _ = trend
    width, height = CHART_SIZE
    keys = flet.Row(
        [
            flet.Text(label, size=11, color=getattr(flet.Colors, SERIES_COLORS[i]))
            for i, label in enumerate(legend)
        ],
        spacing=12,
    )
    canvas = cv.Canvas([lines[name] for name in series], width=width, height=height)
    return _panel(f"Trends: {heading}", [keys, canvas])


class FletViewer:
    """The monitor's viewer, in a Flet window or, with `web`, served to browsers."""

    def __init__(
        self,
        width: int,
        height: int,
        title: str = "YOLO Monitor",
        *,
        web: bool = False,
        host: str = "127.0.0.1",
        port: int = 8550,
    ) -> None:
        """Prepare the viewer; :meth:`run` opens it."""
        require_flet()
        self.width = int(width)
        self.height = int(height)
        self.title = title
        self.web = web
        self.host = host
        self.port = port
        self._changed = threading.Condition()
        self._open = True
        self._seq = 0
        self._snapshot: Snapshot | None = None
        self._history = TrendHistory()
        self._sessions: list[_Session] = []
        self._server: Any = None

    def is_open(self) -> bool:
        """False once the window closed, the server stopped or close() ran."""
        with self._changed:
            return self._open

    def render(
        self,
        frame: np.ndarray,
        *,
        perf_metrics: PerformanceMetrics | None = None,
        sys_stats: SystemStats | None = None,
        proc_stats: dict | None = None,
        camera_info: dict | None = None,
        detections_count: int | None = None,
        classification: dict | None = None,
        log_lines: list[str] | None = None,
        hardware_info: dict | None = None,
        power_info: dict | None = None,
    ) -> None:
        """Hand the newest frame and telemetry to every session; never blocks on drawing."""
        height, width = frame.shape[:2]
        labels = format_labels(
            perf_metrics=perf_metrics,
            sys_stats=sys_stats,
            proc_stats=proc_stats,
            camera_info=camera_info,
            detections_count=detections_count,
            hardware_info=hardware_info,
            power_info=power_info,
            classification=classification,
            frame_size=(width, height),
        )
        with self._changed:
            if not self._open:
                return
            self._history.add(perf_metrics, sys_stats)
            log = "\n".join(log_lines) if log_lines is not None else ""
            self._snapshot = Snapshot(frame, labels, log, self._history.copy())
            self._seq += 1
            self._changed.notify_all()

    def run(self) -> None:
        """Serve until the window closes, Ctrl+C, or close() from the detection thread."""
        flet = require_flet()
        try:
            if self.web:
                server = web_server(self._main, self.host, self.port)
                with self._changed:
                    self._server = server
                    serve = self._open
                if serve:
                    logger.info("Monitor at http://{}:{}", self.host, self.port)
                    server.run()
            else:
                flet.run(self._main, view=flet.AppView.FLET_APP)
                with self._changed:
                    opened = bool(self._sessions)
                if not opened:
                    logger.error(
                        "The Flet window never opened: its client exited first (its "
                        "error is above; on Linux it needs libsecret-1-0). "
                        "--ui opencv uses OpenCV's window instead."
                    )
        finally:
            self._mark_closed()

    def close(self) -> None:
        """Stop drawing, close every window and stop the server."""
        sessions = self._mark_closed()
        if self._server is not None:
            self._server.should_exit = True
        for session in sessions:
            if not session.web:
                with suppress(RuntimeError):
                    asyncio.run_coroutine_threadsafe(
                        session.page.window.close(), session.page.loop
                    )

    def _mark_closed(self) -> list[_Session]:
        with self._changed:
            self._open = False
            self._changed.notify_all()
            return list(self._sessions)

    def _main(self, page: Any) -> None:  # noqa: ANN401 - a flet.Page
        flet = require_flet()
        page.title = self.title
        page.theme = flet.Theme(color_scheme_seed=flet.Colors.GREEN_ACCENT)
        page.dark_theme = flet.Theme(color_scheme_seed=flet.Colors.GREEN_ACCENT)
        page.theme_mode = flet.ThemeMode.DARK
        page.padding = 12
        session = _Session(page)
        if not session.web:
            page.window.width = min(self.width + SIDE_WIDTH + 48, 1600)
            # Tall enough for the side panels, whatever the camera's height.
            page.window.height = min(max(self.height + 48, 900), 1000)
        page.on_close = page.on_disconnect = lambda _event: session.closed.set()
        with self._changed:
            self._sessions.append(session)
        page.add(session.layout(self.title))
        page.run_thread(self.pump, session)

    def next_snapshot(
        self, session: _Session, seen: int
    ) -> tuple[int, Snapshot] | None:
        """Wait for a snapshot newer than `seen`; None once the viewer or the session closed."""
        with self._changed:
            while self._open and not session.closed.is_set() and self._seq == seen:
                self._changed.wait(timeout=0.5)
            if not self._open or session.closed.is_set() or self._snapshot is None:
                return None
            return self._seq, self._snapshot

    def pump(self, session: _Session) -> None:
        """Feed one session the newest frames until it or the viewer closes."""
        seen = 0
        while (latest := self.next_snapshot(session, seen)) is not None:
            seen, snapshot = latest
            try:
                self._push(session, snapshot)
            except TimeoutError:
                logger.debug("Flet session fell behind; dropping a frame")
            except (
                Exception
            ) as exc:  # a closed window or tab ends its pump, not the monitor
                logger.debug("Flet session ended: {}", exc)
                session.closed.set()

    @staticmethod
    def _push(session: _Session, snapshot: Snapshot) -> None:
        payload = frame_payload(snapshot.frame, web=session.web)
        if payload is None:
            logger.warning("Frame encoding failed; skipping frame")
            return
        if isinstance(payload, bytes):
            drawn = session.image.render_encoded(payload)
        else:
            drawn = session.image.render_rgba(*payload)
        future = asyncio.run_coroutine_threadsafe(drawn, session.page.loop)
        try:
            future.result(timeout=FRAME_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
            raise
        session.page.loop.call_soon_threadsafe(session.show, snapshot)
