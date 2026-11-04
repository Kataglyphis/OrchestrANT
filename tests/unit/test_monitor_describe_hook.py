"""The frame loop hands each frame's detections to the describer and stops it; no camera, model or GPU."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest


# monitor imports OpenCV and ONNX Runtime, which the free-threaded leg may not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")
pytest.importorskip("onnxruntime", reason="ONNX Runtime is not on this leg")
monitor = pytest.importorskip("orchestrant.yolo.monitor")


if TYPE_CHECKING:
    from collections.abc import Sequence


class Recorder:
    """Records what the loop hands the describer."""

    def __init__(self) -> None:
        """Start empty."""
        self.frames: list[Sequence[dict[str, object]]] = []
        self.stopped = 0

    def offer(self, detections: Sequence[dict[str, object]]) -> None:
        """Record the frame."""
        self.frames.append(detections)

    def stop(self) -> None:
        """Count the stop."""
        self.stopped += 1


def test_the_loop_offers_each_frame_and_stops_the_describer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop offers each frame and stops the describer."""
    frames = [(True, "frame")]

    def read() -> tuple[bool, str]:
        if not frames:
            raise KeyboardInterrupt
        return frames.pop()

    found = [{"class_id": 15}]
    monkeypatch.setattr(monitor, "preprocess", lambda *_a, **_k: ("blob", 1.0, 0, 0))
    monkeypatch.setattr(monitor, "postprocess", lambda *_a, **_k: (found, None))
    for step in (
        "_update_stats_if_needed",
        "_update_cpu_history",
        "_log_periodic_metrics",
        "_log_session_summary",
    ):
        monkeypatch.setattr(monitor, step, lambda *_a, **_k: None)
    monkeypatch.setattr(monitor, "_update_tracks_if_enabled", lambda *_a: {})
    monkeypatch.setattr(
        monitor, "_draw_frame_if_needed", lambda _c, _s, frame, *_a: frame
    )
    monkeypatch.setattr(monitor, "_render_output", lambda *_a: True)
    monkeypatch.setattr(monitor.cv2, "destroyAllWindows", lambda: None)
    closed = SimpleNamespace(shutdown=lambda: None, release=lambda: None)
    describer = Recorder()
    ctx = SimpleNamespace(
        camera=SimpleNamespace(read=read, release=closed.release),
        perf_tracker=SimpleNamespace(
            tick_camera=lambda: None, add_inference_time=lambda _ms: None
        ),
        session=SimpleNamespace(run=lambda *_a: []),
        args=SimpleNamespace(conf=0.5, debug_boxes=False, debug_output=False),
        input_name="x",
        input_size=(1, 1),
        stretch=False,
        tracks={},
        viewer=None,
        power_monitor=closed,
        sys_monitor=closed,
        describer=describer,
    )
    assert monitor._run_detection_loop(cast("Any", ctx)) == 0  # noqa: SLF001
    assert describer.frames == [found]
    assert describer.stopped == 1
