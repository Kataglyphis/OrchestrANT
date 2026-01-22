from __future__ import annotations

from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from kataglyphispythoninference.yolo.constants import CLASS_NAMES, COLORS
from kataglyphispythoninference.yolo.types import PerformanceMetrics, SystemStats, Track


def _track_color(track_id: int) -> Tuple[int, int, int]:
    r = (track_id * 97) % 255
    g = (track_id * 57) % 255
    b = (track_id * 17) % 255
    return int(b), int(g), int(r)


def draw_2d_running_map(
    frame: np.ndarray,
    tracks: Dict[int, Track],
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
        poly: List[Tuple[int, int]] = []
        for xn, yn in pts:
            px = int(x0 + np.clip(xn, 0.0, 1.0) * (usable_w - 1))
            py = int(usable_top + np.clip(yn, 0.0, 1.0) * (usable_h - 1))
            poly.append((px, py))

        if len(poly) >= 2:
            cv2.polylines(frame, [np.array(poly, dtype=np.int32)], False, color, 2)

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
    cpu_history: Deque[float],
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

    poly: List[Tuple[int, int]] = []
    for i, value in enumerate(values):
        xi = x0 + int(round(i * (w - 1) / max(1, n - 1)))
        yi = y1 - int(round((value / 100.0) * (h - 1)))
        poly.append((xi, yi))

    last_v = values[-1]
    color = get_color_by_percent(last_v)

    if len(poly) >= 2:
        cv2.polylines(frame, [np.array(poly, dtype=np.int32)], False, color, 2)
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


def get_color_by_percent(percent: float, invert: bool = False) -> tuple:
    """Return color based on percentage value."""

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


def draw_detections(
    frame: np.ndarray,
    detections: list,
    perf_metrics: PerformanceMetrics,
    sys_stats: SystemStats,
    proc_stats: dict,
    camera_info: dict,
    cpu_history: Optional[Deque[float]] = None,
    classification: Optional[dict] = None,
    tracks: Optional[Dict[int, Track]] = None,
    map_size: int = 260,
    debug_boxes: bool = False,
    show_stats_panel: bool = True,
    show_detection_panel: bool = True,
) -> np.ndarray:
    """Draw bounding boxes, labels, FPS, and system stats on frame."""

    h, w = frame.shape[:2]

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        class_id = int(det["class_id"])
        score = float(det["score"])

        x1 = int(np.clip(x1, 0, w - 1))
        y1 = int(np.clip(y1, 0, h - 1))
        x2 = int(np.clip(x2, 0, w - 1))
        y2 = int(np.clip(y2, 0, h - 1))
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

    if show_stats_panel:
        panel_height = 320
        panel_width = 450
        cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (0, 0, 0), -1)
        cv2.rectangle(frame, (5, 5), (panel_width, panel_height), (100, 100, 100), 1)

        y_offset = 25
        line_height = 22

        backend_display = camera_info["backend"]
        cv2.putText(
            frame,
            f"--- Capture: {backend_display} ---",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (150, 150, 255),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            "--- Performance ---",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (150, 150, 150),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"Camera Input: {perf_metrics.camera_fps:.1f} FPS",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"Inference Latency: {perf_metrics.inference_ms:.1f} ms/frame",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 255),
            1,
        )
        y_offset += line_height

        budget_color = get_color_by_percent(perf_metrics.frame_budget_percent)
        cv2.putText(
            frame,
            f"Frame Budget Used: {perf_metrics.frame_budget_percent:.1f}%",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            budget_color,
            1,
        )
        y_offset += line_height

        headroom = 100 - perf_metrics.frame_budget_percent
        headroom_color = get_color_by_percent(headroom, invert=True)
        cv2.putText(
            frame,
            f"GPU Headroom: {headroom:.0f}% (capacity: {perf_metrics.inference_capacity_fps:.0f} FPS)",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            headroom_color,
            1,
        )
        y_offset += line_height + 5

        cv2.putText(
            frame,
            "--- System ---",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (150, 150, 150),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"System CPU: {sys_stats.cpu_percent:.1f}%",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            get_color_by_percent(sys_stats.cpu_percent),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"System RAM: {sys_stats.ram_used_gb:.1f}/{sys_stats.ram_total_gb:.1f} GB ({sys_stats.ram_percent:.1f}%)",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            get_color_by_percent(sys_stats.ram_percent),
            1,
        )
        y_offset += line_height

        if sys_stats.gpu_name != "N/A":
            cv2.putText(
                frame,
                f"GPU Load: {sys_stats.gpu_percent:.0f}%  |  Temp: {sys_stats.gpu_temp_celsius:.0f}C  |  {sys_stats.gpu_power_watts:.0f}W",
                (10, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                get_color_by_percent(sys_stats.gpu_percent),
                1,
            )
            y_offset += line_height

            cv2.putText(
                frame,
                f"VRAM: {sys_stats.gpu_memory_used_gb:.1f}/{sys_stats.gpu_memory_total_gb:.1f} GB ({sys_stats.gpu_memory_percent:.0f}%)",
                (10, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                get_color_by_percent(sys_stats.gpu_memory_percent),
                1,
            )
            y_offset += line_height + 5

        cv2.putText(
            frame,
            "--- Process ---",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (150, 150, 150),
            1,
        )
        y_offset += line_height

        cv2.putText(
            frame,
            f"CPU: {proc_stats['cpu_percent']:.1f}%  |  RAM: {proc_stats['memory_mb']:.0f}MB  |  Threads: {proc_stats['threads']}",
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            get_color_by_percent(min(proc_stats["cpu_percent"], 100)),
            1,
        )

        bar_y = panel_height - 15
        bar_width = panel_width - 20
        bar_height = 8

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
        det_text = f"Detections: {len(detections)}"
        (tw, th), _ = cv2.getTextSize(det_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
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
            (ctw, cth), _ = cv2.getTextSize(cls_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.putText(
                frame,
                cls_text,
                (frame.shape[1] - ctw - 10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

    if show_stats_panel:
        cv2.rectangle(
            frame, (10, bar_y), (10 + bar_width, bar_y + bar_height), (50, 50, 50), -1
        )
        fill_width = int(bar_width * min(perf_metrics.frame_budget_percent, 100) / 100)
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

    if tracks:
        draw_2d_running_map(frame, tracks, map_size=map_size)

    return frame
