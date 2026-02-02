"""Shared data structures for monitoring pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections import deque


class CaptureBackend(Enum):
    """Video capture backend options."""

    OPENCV = "opencv"
    GSTREAMER = "gstreamer"


@dataclass
class CameraConfig:
    """Camera configuration settings."""

    device_index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    backend: CaptureBackend = CaptureBackend.OPENCV


@dataclass
class SystemStats:
    """Container for system statistics."""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_percent: float = 0.0
    gpu_memory_used_gb: float = 0.0
    gpu_memory_total_gb: float = 0.0
    gpu_memory_percent: float = 0.0
    gpu_temp_celsius: float = 0.0
    gpu_power_watts: float = 0.0
    gpu_name: str = "N/A"


@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""

    camera_fps: float = 0.0
    inference_ms: float = 0.0
    inference_capacity_fps: float = 0.0
    frame_budget_percent: float = 0.0
    actual_throughput_fps: float = 0.0


@dataclass
class Track:
    """Tracked object with normalized trajectory points."""

    track_id: int
    points_norm: deque[tuple[float, float]]
    last_seen_ts: float
