"""Shared view-model that formats raw metrics into display strings.

Both :class:`DearPyGuiViewer` and :class:`WxPythonViewer` delegate to
this module so that formatting logic is defined in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from orchestr_ant_ion.pipeline.types import (
        PerformanceMetrics,
        SystemStats,
    )


@dataclass(slots=True)
class ViewLabels:
    """Pre-formatted label strings ready for display."""

    resolution: str = ""
    capture: str = ""
    backend: str = ""
    pipeline: str = ""
    cpu_model: str = ""
    ram_total: str = ""
    gpu_model: str = ""
    vram_total: str = ""
    detections: str = ""
    camera_fps: str = ""
    inference_ms: str = ""
    budget: str = ""
    headroom: str = ""
    sys_cpu: str = ""
    sys_ram: str = ""
    gpu: str = ""
    vram: str = ""
    power: str = ""
    energy: str = ""
    proc: str = ""
    classification: str = ""


def format_labels(  # noqa: C901
    *,
    perf_metrics: PerformanceMetrics | None,
    sys_stats: SystemStats | None,
    proc_stats: dict | None,
    camera_info: dict | None,
    detections_count: int | None,
    hardware_info: dict | None,
    power_info: dict | None,
    classification: dict | None,
    frame_size: tuple[int, int],
) -> ViewLabels:
    """Return a :class:`ViewLabels` from the raw data sources."""
    labels = ViewLabels()

    if (
        perf_metrics is None
        or sys_stats is None
        or proc_stats is None
        or camera_info is None
    ):
        return labels

    w, h = frame_size
    labels.resolution = f"Resolution: {w}x{h}"

    # Camera / backend
    backend_display = camera_info.get("backend", "unknown")
    labels.capture = f"Capture: {backend_display}"
    labels.backend = f"Backend: {backend_display}"
    pipeline = camera_info.get("pipeline", "")
    if pipeline:
        if len(pipeline) > 70:
            pipeline = pipeline[:67] + "..."
        labels.pipeline = f"Pipeline: {pipeline}"
    if detections_count is not None:
        labels.detections = f"Detections: {detections_count}"

    # Hardware
    if hardware_info is not None:
        cpu_model = hardware_info.get("cpu_model", "N/A")
        ram_total = hardware_info.get("ram_total_gb", 0.0)
        gpu_model = hardware_info.get("gpu_model", "N/A")
        vram_total = hardware_info.get("vram_total_gb", 0.0)
        labels.cpu_model = f"CPU: {cpu_model}"
        labels.ram_total = f"RAM: {ram_total:.1f} GB"
        if gpu_model and gpu_model != "N/A":
            labels.gpu_model = f"GPU: {gpu_model}"
            labels.vram_total = f"VRAM: {vram_total:.1f} GB"

    # Performance
    labels.camera_fps = f"Camera Input: {perf_metrics.camera_fps:.1f} FPS"
    labels.inference_ms = f"Inference Latency: {perf_metrics.inference_ms:.1f} ms/frame"
    labels.budget = f"Frame Budget Used: {perf_metrics.frame_budget_percent:.1f}%"
    headroom = 100 - perf_metrics.frame_budget_percent
    labels.headroom = (
        f"GPU Headroom: {headroom:.0f}% "
        f"(capacity: {perf_metrics.inference_capacity_fps:.0f} FPS)"
    )

    # System
    labels.sys_cpu = f"System CPU: {sys_stats.cpu_percent:.1f}%"
    labels.sys_ram = (
        f"System RAM: {sys_stats.ram_used_gb:.1f}/{sys_stats.ram_total_gb:.1f} GB "
        f"({sys_stats.ram_percent:.1f}%)"
    )
    if sys_stats.gpu_name != "N/A":
        labels.gpu = (
            f"GPU Load: {sys_stats.gpu_percent:.0f}% | "
            f"Temp: {sys_stats.gpu_temp_celsius:.0f}C | "
            f"{sys_stats.gpu_power_watts:.0f}W"
        )
        labels.vram = (
            f"VRAM: {sys_stats.gpu_memory_used_gb:.1f}/"
            f"{sys_stats.gpu_memory_total_gb:.1f} GB "
            f"({sys_stats.gpu_memory_percent:.0f}%)"
        )

    # Power
    if power_info is not None:
        system_power = power_info.get("system_power_watts", 0.0)
        cpu_power = power_info.get("cpu_power_watts", 0.0)
        gpu_power = power_info.get("gpu_power_watts", 0.0)
        energy_wh = power_info.get("energy_wh", 0.0)
        if system_power > 0.0:
            labels.power = (
                f"Power: {system_power:.0f}W "
                f"(CPU {cpu_power:.0f}W, GPU {gpu_power:.0f}W)"
            )
        elif gpu_power > 0.0:
            labels.power = f"Power: GPU {gpu_power:.0f}W"
        if energy_wh > 0.0:
            labels.energy = f"Energy: {energy_wh:.3f} Wh"

    # Process
    labels.proc = (
        f"CPU: {proc_stats['cpu_percent']:.1f}% | "
        f"RAM: {proc_stats['memory_mb']:.0f}MB | "
        f"Threads: {proc_stats['threads']}"
    )

    # Classification
    if classification is not None:
        class_label = classification.get("label", "unknown")
        class_score = classification.get("score", 0.0)
        labels.classification = f"Class: {class_label} ({class_score:.2f})"

    return labels
