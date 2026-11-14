"""Performance tracking helpers for monitoring pipelines."""

from __future__ import annotations

import time
from collections import deque

from orchestrant.pipeline.types import PerformanceMetrics


# Where a frame's time goes, in loop order; the session summary prints p50 and p95 of each.
STAGES = ("capture_wait", "preprocess", "inference", "decode", "draw", "present")
# About five minutes at 30 FPS: a long session's summary speaks for its recent frames and stays small.
STAGE_SAMPLES = 10_000


class PerformanceTracker:
    """Track performance metrics with moving averages."""

    def __init__(self, avg_frames: int = 30, nominal_fps: float = 0.0) -> None:
        """Initialize the tracker with a rolling window size and the camera's own rate (0: unknown)."""
        self.avg_frames = avg_frames
        self.nominal_fps = max(0.0, nominal_fps)
        self.camera_times: deque[float] = deque(maxlen=avg_frames)
        self.inference_times: deque[float] = deque(maxlen=avg_frames)
        self.last_camera_time: float | None = None
        self.frame_count = 0
        self.start_time = time.perf_counter()
        self.stage_times: dict[str, deque[float]] = {
            name: deque(maxlen=STAGE_SAMPLES) for name in STAGES
        }

    def tick_camera(self) -> None:
        """Record a camera frame tick for FPS estimation."""
        now = time.perf_counter()
        if self.last_camera_time is not None:
            self.camera_times.append(now - self.last_camera_time)
        self.last_camera_time = now
        self.frame_count += 1

    def add_inference_time(self, elapsed_ms: float) -> None:
        """Record a single inference duration in milliseconds."""
        self.inference_times.append(elapsed_ms)

    def add_stage(self, name: str, elapsed_ms: float) -> None:
        """Record one frame's milliseconds in one of STAGES."""
        self.stage_times[name].append(elapsed_ms)

    def stage_summary(self) -> dict[str, tuple[float, float]]:
        """p50 and p95 milliseconds of each stage that saw a frame, by the nearest rank."""
        summary = {}
        for name, samples in self.stage_times.items():
            if samples:
                ranked = sorted(samples)
                p95 = ranked[min(len(ranked) - 1, int(0.95 * len(ranked)))]
                summary[name] = (ranked[len(ranked) // 2], p95)
        return summary

    def get_metrics(self) -> PerformanceMetrics:
        """Compute aggregated performance metrics."""
        metrics = PerformanceMetrics()

        if self.camera_times:
            avg_camera_time = sum(self.camera_times) / len(self.camera_times)
            metrics.camera_fps = 1.0 / avg_camera_time if avg_camera_time > 0 else 0.0
        # The loop's own rate already contains the inference, so a budget against it never passes 100 %.
        metrics.source_fps = self.nominal_fps or metrics.camera_fps

        if self.inference_times:
            metrics.inference_ms = sum(self.inference_times) / len(self.inference_times)
            metrics.inference_capacity_fps = (
                1000.0 / metrics.inference_ms if metrics.inference_ms > 0 else 0.0
            )

            if metrics.source_fps > 0:
                frame_budget_ms = 1000.0 / metrics.source_fps
                metrics.frame_budget_percent = (
                    metrics.inference_ms / frame_budget_ms
                ) * 100

        elapsed = time.perf_counter() - self.start_time
        metrics.actual_throughput_fps = (
            self.frame_count / elapsed if elapsed > 0 else 0.0
        )

        return metrics
