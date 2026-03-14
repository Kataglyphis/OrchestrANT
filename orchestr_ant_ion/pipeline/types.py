"""Shared data structures for monitoring pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections import deque


RESOLUTION_MIN = 64
RESOLUTION_MAX = 7680
FPS_MIN = 1
FPS_MAX = 240
DEVICE_INDEX_MIN = 0


class CaptureBackend(Enum):
    """Video capture backend options."""

    OPENCV = "opencv"
    GSTREAMER = "gstreamer"


@dataclass
class CameraConfig:
    """Camera configuration settings with validation."""

    device_index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    backend: CaptureBackend = CaptureBackend.OPENCV

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.device_index < DEVICE_INDEX_MIN:
            raise ValueError(f"device_index must be >= {DEVICE_INDEX_MIN}")
        if not RESOLUTION_MIN <= self.width <= RESOLUTION_MAX:
            raise ValueError(f"width must be in [{RESOLUTION_MIN}, {RESOLUTION_MAX}]")
        if not RESOLUTION_MIN <= self.height <= RESOLUTION_MAX:
            raise ValueError(f"height must be in [{RESOLUTION_MIN}, {RESOLUTION_MAX}]")
        if not FPS_MIN <= self.fps <= FPS_MAX:
            raise ValueError(f"fps must be in [{FPS_MIN}, {FPS_MAX}]")


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
    """Tracked object with normalized trajectory points.

    Attributes:
        track_id: Unique identifier for this track.
        points_norm: Deque of (x, y) normalized coordinates (0.0-1.0) showing
            the object's recent trajectory. New points are appended to the right.
        last_seen_ts: Timestamp of the most recent detection associated with
            this track. Used to expire stale tracks.
    """

    track_id: int
    points_norm: deque[tuple[float, float]]
    last_seen_ts: float
