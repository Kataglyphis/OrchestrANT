"""Overlay drawing utilities for detections and telemetry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np
from loguru import logger

from orchestrant.pipeline.ui.viewmodel import format_labels
from orchestrant.yolo.core.constants import CLASS_NAMES, COLORS


if TYPE_CHECKING:
    from collections import deque

    from orchestrant.pipeline.types import (
        PerformanceMetrics,
        SystemStats,
        Track,
    )
    from orchestrant.pipeline.ui.viewmodel import ViewLabels
    from orchestrant.yolo.core.postprocess import Detection


def _track_color(track_id: int) -> tuple[int, int, int]:
    """Return a deterministic color for a track id."""
    r = (track_id * 97) % 255
    g = (track_id * 57) % 255
    b = (track_id * 17) % 255
    return int(b), int(g), int(r)


def draw_2d_running_map(
    frame: np.ndarray,
    tracks: dict[int, Track],
    *,
    map_size: int = 260,
    margin: int = 10,
) -> None:
    """Draw a simple top-down minimap of tracked centroids with motion trails."""
    if frame is None or frame.size == 0:
        return

    h, w = frame.shape[:2]
    size = int(map_size)
    x0 = max(margin, w - size - margin)
    y0 = margin
    x1 = min(w - margin, x0 + size)
    y1 = min(h - margin, y0 + size)

    cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 0), -1)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (100, 100, 100), 1)
    cv2.putText(
        frame,
        "2D running (persons)",
        (x0 + 8, y0 + 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )

    for tick in (0.25, 0.5, 0.75):
        xt = int(x0 + tick * (x1 - x0))
        yt = int(y0 + tick * (y1 - y0))
        cv2.line(frame, (xt, y0 + 24), (xt, y1), (35, 35, 35), 1)
        cv2.line(frame, (x0, yt), (x1, yt), (35, 35, 35), 1)

    usable_top = y0 + 26
    usable_h = max(1, y1 - usable_top)
    usable_w = max(1, x1 - x0)

    for tid, track in tracks.items():
        pts = list(track.points_norm)
        if not pts:
            continue

        color = _track_color(tid)
        poly: list[tuple[int, int]] = []
        for xn, yn in pts:
            px = int(x0 + np.clip(xn, 0.0, 1.0) * (usable_w - 1))
            py = int(usable_top + np.clip(yn, 0.0, 1.0) * (usable_h - 1))
            poly.append((px, py))

        if len(poly) >= 2:
            cv2.polylines(
                frame,
                [np.array(poly, dtype=np.int32)],
                isClosed=False,
                color=color,
                thickness=2,
            )

        cx, cy = poly[-1]
        cv2.circle(frame, (cx, cy), 4, color, -1)
        cv2.putText(
            frame,
            str(tid),
            (cx + 6, cy - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )


def draw_cpu_process_history_plot(
    frame: np.ndarray,
    cpu_history: deque[float],
    *,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    """Draw a simple 2D line chart of process CPU% history inside a rectangle."""
    if frame is None or frame.size == 0:
        return

    if w < 30 or h < 20:
        return

    if cpu_history is None or len(cpu_history) < 1:
        return

    x0, y0 = int(x), int(y)
    x1, y1 = int(x + w), int(y + h)

    cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 0), -1)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (100, 100, 100), 1)

    for tick in (0.25, 0.5, 0.75):
        xt = int(x0 + tick * (x1 - x0))
        yt = int(y0 + tick * (y1 - y0))
        cv2.line(frame, (xt, y0), (xt, y1), (35, 35, 35), 1)
        cv2.line(frame, (x0, yt), (x1, yt), (35, 35, 35), 1)

    values = list(cpu_history)
    n = len(values)

    values = [float(np.clip(v, 0.0, 100.0)) for v in values]

    poly: list[tuple[int, int]] = []
    for i, value in enumerate(values):
        xi = x0 + round(i * (w - 1) / max(1, n - 1))
        yi = y1 - round((value / 100.0) * (h - 1))
        poly.append((xi, yi))

    last_v = values[-1]
    color = get_color_by_percent(percent=last_v)

    if len(poly) >= 2:
        cv2.polylines(
            frame,
            [np.array(poly, dtype=np.int32)],
            isClosed=False,
            color=color,
            thickness=2,
        )
    cv2.circle(frame, poly[-1], 3, color, -1)

    label = f"Proc CPU history ({n}): {last_v:.1f}%"
    cv2.putText(
        frame,
        label,
        (x0 + 6, y0 - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )


def get_color_by_percent(
    *, percent: float, invert: bool = False
) -> tuple[int, int, int]:
    """Return a color for a percentage value."""
    if invert:
        if percent > 50:
            return (0, 255, 0)
        if percent > 20:
            return (0, 165, 255)
        return (0, 0, 255)
    if percent < 70:
        return (0, 255, 0)
    if percent < 90:
        return (0, 165, 255)
    return (0, 0, 255)


def _draw_detection_boxes(
    frame: np.ndarray,
    detections: list[Detection],
    *,
    debug_boxes: bool,
) -> None:
    h, w = frame.shape[:2]

    def _clamp_int(value: float, low: int, high: int) -> int:
        return max(low, min(high, int(value)))

    for det in detections:
        x1, y1, x2, y2 = (float(value) for value in det["bbox"][:4])
        class_id = int(det["class_id"])
        score = float(det["score"])

        x1 = _clamp_int(x1, 0, w - 1)
        y1 = _clamp_int(y1, 0, h - 1)
        x2 = _clamp_int(x2, 0, w - 1)
        y2 = _clamp_int(y2, 0, h - 1)
        if x2 <= x1 or y2 <= y1:
            if debug_boxes:
                logger.info(
                    "Skipping invalid bbox: {}",
                    [x1, y1, x2, y2],
                )
            continue

        color = tuple(map(int, COLORS[class_id % len(COLORS)]))
        if class_id < len(CLASS_NAMES):
            label = f"{CLASS_NAMES[class_id]}: {score:.2f}"
        else:
            label = f"class {class_id}: {score:.2f}"

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 10), (x1 + tw, y1), color, -1)
        cv2.putText(
            frame,
            label,
            (x1, y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )


_ROW_GAP = 22
_SECTION_GAP = _ROW_GAP + 5
_HEADING_SCALE = 0.5
_VALUE_SCALE = 0.55
_HEADING_COLOR = (150, 150, 150)


def _performance_rows(
    labels: ViewLabels,
    perf_metrics: PerformanceMetrics,
    budget_color: tuple[int, int, int],
) -> list[tuple[str, float, tuple[int, int, int], int]]:
    headroom = get_color_by_percent(percent=perf_metrics.headroom_percent, invert=True)
    return [
        ("--- Performance ---", _HEADING_SCALE, _HEADING_COLOR, _ROW_GAP),
        (labels.camera_fps, _VALUE_SCALE, (0, 255, 0), _ROW_GAP),
        (labels.inference_ms, _VALUE_SCALE, (0, 255, 255), _ROW_GAP),
        (labels.budget, _VALUE_SCALE, budget_color, _ROW_GAP),
        (labels.headroom, _VALUE_SCALE, headroom, _SECTION_GAP),
    ]


def _system_rows(
    labels: ViewLabels, sys_stats: SystemStats
) -> list[tuple[str, float, tuple[int, int, int], int]]:
    rows = [
        ("--- System ---", _HEADING_SCALE, _HEADING_COLOR, _ROW_GAP),
        (
            labels.sys_cpu,
            _VALUE_SCALE,
            get_color_by_percent(percent=sys_stats.cpu_percent),
            _ROW_GAP,
        ),
        (
            labels.sys_ram,
            _VALUE_SCALE,
            get_color_by_percent(percent=sys_stats.ram_percent),
            _ROW_GAP,
        ),
    ]
    if labels.gpu:
        rows += [
            (
                labels.gpu,
                _VALUE_SCALE,
                get_color_by_percent(percent=sys_stats.gpu_percent),
                _ROW_GAP,
            ),
            (
                labels.vram,
                _VALUE_SCALE,
                get_color_by_percent(percent=sys_stats.gpu_memory_percent),
                _SECTION_GAP,
            ),
        ]
    return rows


def _process_rows(
    labels: ViewLabels, proc_stats: dict[str, float | int]
) -> list[tuple[str, float, tuple[int, int, int], int]]:
    percent = min(proc_stats["cpu_percent"], 100)
    return [
        ("--- Process ---", _HEADING_SCALE, _HEADING_COLOR, _ROW_GAP),
        (labels.proc, _VALUE_SCALE, get_color_by_percent(percent=percent), _ROW_GAP),
    ]


def panel_rows(
    perf_metrics: PerformanceMetrics,
    sys_stats: SystemStats,
    proc_stats: dict[str, float | int],
    camera_info: dict[str, object],
    budget_color: tuple[int, int, int],
) -> list[tuple[str, float, tuple[int, int, int], int]]:
    """The panel's rows, their text from the viewer's ViewLabels, so a window and a browser read the same."""
    labels = format_labels(
        perf_metrics=perf_metrics,
        sys_stats=sys_stats,
        proc_stats=proc_stats,
        camera_info=camera_info,
        detections_count=None,
        hardware_info=None,
        power_info=None,
        classification=None,
        frame_size=(0, 0),
    )
    return [
        (
            f"--- Capture: {camera_info['backend']} ---",
            _HEADING_SCALE,
            (150, 150, 255),
            _ROW_GAP,
        ),
        *_performance_rows(labels, perf_metrics, budget_color),
        *_system_rows(labels, sys_stats),
        *_process_rows(labels, proc_stats),
    ]


def draw_stats_panel(
    frame: np.ndarray,
    perf_metrics: PerformanceMetrics,
    sys_stats: SystemStats,
    proc_stats: dict[str, float | int],
    camera_info: dict[str, object],
) -> tuple[tuple[int, int, int], int, int, int]:
    """Draw the telemetry panel; return the budget colour and the bar's geometry."""
    panel_height = 320
    panel_width = 450
    cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (0, 0, 0), -1)
    cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (100, 100, 100), 1)

    budget_color = get_color_by_percent(percent=perf_metrics.frame_budget_percent)
    rows = panel_rows(perf_metrics, sys_stats, proc_stats, camera_info, budget_color)
    y_offset = 25
    for text, scale, color, gap in rows:
        cv2.putText(
            frame, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1
        )
        y_offset += gap

    bar_y = panel_height - 15
    bar_width = panel_width - 20
    bar_height = 8
    return budget_color, bar_y, bar_width, bar_height


def _draw_detection_panel(
    frame: np.ndarray,
    detections: list[Detection],
    classification: dict[str, object] | None,
) -> None:
    det_text = f"Detections: {len(detections)}"
    (tw, _th), _ = cv2.getTextSize(det_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.putText(
        frame,
        det_text,
        (frame.shape[1] - tw - 10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 255),
        2,
    )

    if classification is not None:
        class_label = classification.get("label", "unknown")
        class_score = classification.get("score", 0.0)
        cls_text = f"Class: {class_label} ({class_score:.2f})"
        (ctw, _cth), _ = cv2.getTextSize(cls_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.putText(
            frame,
            cls_text,
            (frame.shape[1] - ctw - 10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )


def _draw_budget_bar(
    frame: np.ndarray,
    *,
    budget_color: tuple[int, int, int],
    bar_y: int,
    bar_width: int,
    bar_height: int,
    frame_budget_percent: float,
) -> None:
    cv2.rectangle(
        frame, (10, bar_y), (10 + bar_width, bar_y + bar_height), (50, 50, 50), -1
    )
    fill_width = int(bar_width * min(frame_budget_percent, 100) / 100)
    cv2.rectangle(
        frame,
        (10, bar_y),
        (10 + fill_width, bar_y + bar_height),
        budget_color,
        -1,
    )
    cv2.rectangle(
        frame,
        (10, bar_y),
        (10 + bar_width, bar_y + bar_height),
        (100, 100, 100),
        1,
    )


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection],
    perf_metrics: PerformanceMetrics,
    sys_stats: SystemStats,
    proc_stats: dict[str, float | int],
    camera_info: dict[str, object],
    *,
    cpu_history: deque[float] | None = None,
    classification: dict[str, object] | None = None,
    tracks: dict[int, Track] | None = None,
    map_size: int = 260,
    debug_boxes: bool = False,
    show_stats_panel: bool = True,
    show_detection_panel: bool = True,
) -> np.ndarray:
    """Draw bounding boxes, labels, FPS, and system stats on frame."""
    _draw_detection_boxes(frame, detections, debug_boxes=debug_boxes)

    budget_color = (0, 0, 0)
    bar_y = 0
    bar_width = 0
    bar_height = 0
    if show_stats_panel:
        budget_color, bar_y, bar_width, bar_height = draw_stats_panel(
            frame,
            perf_metrics,
            sys_stats,
            proc_stats,
            camera_info,
        )

    if cpu_history is not None and show_stats_panel:
        overlay_w = 320
        overlay_h = 110
        margin = 10
        overlay_x = max(margin, frame.shape[1] - overlay_w - margin)
        overlay_y = 40
        draw_cpu_process_history_plot(
            frame,
            cpu_history,
            x=overlay_x,
            y=overlay_y,
            w=overlay_w,
            h=overlay_h,
        )

    if show_detection_panel:
        _draw_detection_panel(frame, detections, classification)

    if show_stats_panel:
        _draw_budget_bar(
            frame,
            budget_color=budget_color,
            bar_y=bar_y,
            bar_width=bar_width,
            bar_height=bar_height,
            frame_budget_percent=perf_metrics.frame_budget_percent,
        )

    if tracks:
        draw_2d_running_map(frame, tracks, map_size=map_size)

    return frame
