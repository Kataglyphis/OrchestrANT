"""Performance tracking helpers for monitoring pipelines."""

from __future__ import annotations

import time

from orchestr_ant_ion.pipeline.types import PerformanceMetrics


class PerformanceTracker:
    """Track performance metrics with moving averages."""

    def __init__(self, avg_frames: int = 30) -> None:
        """Initialize the tracker with a rolling window size."""
        self.avg_frames = avg_frames
        self.camera_times: list[float] = []
        self.inference_times: list[float] = []
        self.last_camera_time: float | None = None
        self.frame_count = 0
        self.start_time = time.perf_counter()

    def tick_camera(self) -> None:
        """Record a camera frame tick for FPS estimation."""
        now = time.perf_counter()
        if self.last_camera_time is not None:
            self.camera_times.append(now - self.last_camera_time)
            if len(self.camera_times) > self.avg_frames:
                self.camera_times.pop(0)
        self.last_camera_time = now
        self.frame_count += 1

    def add_inference_time(self, elapsed_ms: float) -> None:
        """Record a single inference duration in milliseconds."""
        self.inference_times.append(elapsed_ms)
        if len(self.inference_times) > self.avg_frames:
            self.inference_times.pop(0)

    def get_metrics(self) -> PerformanceMetrics:
        """Compute aggregated performance metrics."""
        metrics = PerformanceMetrics()

        if self.camera_times:
            avg_camera_time = sum(self.camera_times) / len(self.camera_times)
            metrics.camera_fps = 1.0 / avg_camera_time if avg_camera_time > 0 else 0.0

        if self.inference_times:
            metrics.inference_ms = sum(self.inference_times) / len(self.inference_times)
            metrics.inference_capacity_fps = (
                1000.0 / metrics.inference_ms if metrics.inference_ms > 0 else 0.0
            )

            if metrics.camera_fps > 0:
                frame_budget_ms = 1000.0 / metrics.camera_fps
                metrics.frame_budget_percent = (
                    metrics.inference_ms / frame_budget_ms
                ) * 100

        elapsed = time.perf_counter() - self.start_time
        metrics.actual_throughput_fps = (
            self.frame_count / elapsed if elapsed > 0 else 0.0
        )

        return metrics

