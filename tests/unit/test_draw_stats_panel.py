"""The stats panel's rows, positions and colours; it does not read the rendered glyphs."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest


# draw imports OpenCV, which the free-threaded leg does not install.
pytest.importorskip("cv2", reason="OpenCV has no free-threaded wheel")

from orchestrant.pipeline.types import PerformanceMetrics, SystemStats
from orchestrant.yolo.ui import draw


PERF = PerformanceMetrics(
    camera_fps=30.0,
    inference_ms=12.5,
    frame_budget_percent=40.0,
    inference_capacity_fps=80.0,
)
SYS = SystemStats(
    cpu_percent=25.0,
    ram_used_gb=8.0,
    ram_total_gb=16.0,
    ram_percent=50.0,
    gpu_name="RTX",
    gpu_percent=90.0,
    gpu_temp_celsius=70.0,
    gpu_power_watts=200.0,
    gpu_memory_used_gb=4.0,
    gpu_memory_total_gb=8.0,
    gpu_memory_percent=50.0,
)
PROC = {"cpu_percent": 150.0, "memory_mb": 512.0, "threads": 9}


def _rows(monkeypatch: pytest.MonkeyPatch, sys_stats: SystemStats) -> list[tuple]:
    calls = []
    monkeypatch.setattr(
        draw.cv2,
        "putText",
        lambda _frame, text, origin, _font, scale, _color, _width: calls.append(
            (text, origin[1], scale)
        ),
    )
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    draw.draw_stats_panel(frame, PERF, sys_stats, PROC, {"backend": "opencv"})
    return calls


def test_gpu_rows_and_section_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPU adds its two rows, and each section's last row leaves the wider gap."""
    assert _rows(monkeypatch, SYS) == [
        ("--- Capture: opencv ---", 25, 0.5),
        ("--- Performance ---", 47, 0.5),
        ("Camera Input: 30.0 FPS", 69, 0.55),
        ("Inference Latency: 12.5 ms/frame", 91, 0.55),
        ("Frame Budget Used: 40.0%", 113, 0.55),
        ("GPU Headroom: 60% (capacity: 80 FPS)", 135, 0.55),
        ("--- System ---", 162, 0.5),
        ("System CPU: 25.0%", 184, 0.55),
        ("System RAM: 8.0/16.0 GB (50.0%)", 206, 0.55),
        ("GPU Load: 90%  |  Temp: 70C  |  200W", 228, 0.55),
        ("VRAM: 4.0/8.0 GB (50%)", 250, 0.55),
        ("--- Process ---", 277, 0.5),
        ("CPU: 150.0%  |  RAM: 512MB  |  Threads: 9", 299, 0.55),
    ]


def test_no_gpu_rows_without_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a GPU the process section moves up by the rows and the gap."""
    rows = _rows(monkeypatch, replace(SYS, gpu_name="N/A"))
    assert [
        text for text, _y, _scale in rows if "GPU Load" in text or "VRAM" in text
    ] == []
    assert rows[-2:] == [
        ("--- Process ---", 228, 0.5),
        ("CPU: 150.0%  |  RAM: 512MB  |  Threads: 9", 250, 0.55),
    ]


def test_process_colour_caps_at_100(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several cores' CPU share colours as 100 %, and the bar geometry is returned."""
    colours = []
    monkeypatch.setattr(
        draw.cv2,
        "putText",
        lambda _frame, _text, _origin, _font, _scale, color, _width: colours.append(
            color
        ),
    )
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    budget, bar_y, bar_width, bar_height = draw.draw_stats_panel(
        frame, PERF, SYS, PROC, {"backend": "opencv"}
    )
    assert colours[-1] == draw.get_color_by_percent(percent=100)
    assert budget == draw.get_color_by_percent(percent=40.0)
    assert (bar_y, bar_width, bar_height) == (305, 430, 8)
