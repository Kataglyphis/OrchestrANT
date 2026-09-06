"""Unit tests for the pipeline performance tracker (deterministic clock)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrant.pipeline.metrics import performance
from orchestrant.pipeline.metrics.performance import PerformanceTracker


if TYPE_CHECKING:
    import pytest


class FakeClock:
    """Deterministic stand-in for time.perf_counter."""

    def __init__(self) -> None:
        """Start the fake clock at t=0."""
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        """Move the clock forward by the given number of seconds."""
        self.now += seconds

    def __call__(self) -> float:
        """Return the current fake time, mimicking perf_counter()."""
        return self.now


def make_tracker(
    monkeypatch: pytest.MonkeyPatch, **kwargs: int
) -> tuple[PerformanceTracker, FakeClock]:
    """Build a tracker whose clock the test fully controls."""
    clock = FakeClock()
    monkeypatch.setattr(performance.time, "perf_counter", clock)
    return PerformanceTracker(**kwargs), clock


class TestCameraFps:
    """Camera FPS derives from inter-tick deltas."""

    def test_steady_ticks_yield_exact_fps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ticks every 50 ms must report 20 FPS."""
        tracker, clock = make_tracker(monkeypatch)
        for _ in range(5):
            tracker.tick_camera()
            clock.advance(0.05)
        metrics = tracker.get_metrics()
        assert abs(metrics.camera_fps - 20.0) < 1e-6

    def test_no_ticks_reports_zero_fps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fresh tracker has no camera history and no throughput."""
        tracker, _clock = make_tracker(monkeypatch)
        metrics = tracker.get_metrics()
        assert metrics.camera_fps == 0.0

    def test_rolling_window_caps_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only the newest avg_frames deltas participate in the average."""
        tracker, clock = make_tracker(monkeypatch, avg_frames=2)
        tracker.tick_camera()
        clock.advance(1.0)  # slow delta, must be evicted
        tracker.tick_camera()
        clock.advance(0.1)
        tracker.tick_camera()
        clock.advance(0.1)
        tracker.tick_camera()
        metrics = tracker.get_metrics()
        assert abs(metrics.camera_fps - 10.0) < 1e-6


class TestInferenceMetrics:
    """Inference timing aggregation."""

    def test_average_and_capacity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """20 ms average inference means 50 FPS capacity."""
        tracker, _clock = make_tracker(monkeypatch)
        tracker.add_inference_time(10.0)
        tracker.add_inference_time(30.0)
        metrics = tracker.get_metrics()
        assert metrics.inference_ms == 20.0
        assert abs(metrics.inference_capacity_fps - 50.0) < 1e-6

    def test_frame_budget_percent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """25 ms inference at 20 FPS (50 ms budget) is 50% of the budget."""
        tracker, clock = make_tracker(monkeypatch)
        for _ in range(3):
            tracker.tick_camera()
            clock.advance(0.05)
        tracker.add_inference_time(25.0)
        metrics = tracker.get_metrics()
        assert abs(metrics.frame_budget_percent - 50.0) < 1e-6


class TestThroughput:
    """End-to-end throughput over wall time."""

    def test_actual_throughput_counts_all_frames(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """8 frames over 2 seconds is 4 FPS regardless of window size."""
        tracker, clock = make_tracker(monkeypatch, avg_frames=2)
        for _ in range(8):
            tracker.tick_camera()
            clock.advance(0.25)
        metrics = tracker.get_metrics()
        assert abs(metrics.actual_throughput_fps - 4.0) < 1e-6
