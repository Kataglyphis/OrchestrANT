"""The monitor's periodic system, process, power and capacity log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger


if TYPE_CHECKING:
    from orchestrant.pipeline.types import PerformanceMetrics
    from orchestrant.yolo.monitor import RuntimeState


def log_resources(metrics: PerformanceMetrics, state: RuntimeState) -> None:
    """Log the slower system, process, power and capacity block."""
    logger.info(
        "System CPU: {:.1f}% | RAM: {:.1f}/{:.1f}GB",
        state.sys_stats.cpu_percent,
        state.sys_stats.ram_used_gb,
        state.sys_stats.ram_total_gb,
    )

    if state.sys_stats.gpu_name != "N/A":
        logger.info(
            "GPU: {:.0f}% | VRAM: {:.1f}/{:.1f}GB | Temp: {:.0f}Â°C",
            state.sys_stats.gpu_percent,
            state.sys_stats.gpu_memory_used_gb,
            state.sys_stats.gpu_memory_total_gb,
            state.sys_stats.gpu_temp_celsius,
        )

    logger.info(
        "Process: CPU {:.1f}% | {:.0f}MB RAM",
        state.proc_stats["cpu_percent"],
        state.proc_stats["memory_mb"],
    )

    if state.power_info["system_power_watts"] > 0.0:
        logger.info(
            "Power (est): {:.0f}W | CPU {:.0f}W | GPU {:.0f}W | Energy {:.3f}Wh",
            state.power_info["system_power_watts"],
            state.power_info["cpu_power_watts"],
            state.power_info["gpu_power_watts"],
            state.power_info["energy_wh"],
        )
    elif state.power_info["gpu_power_watts"] > 0.0:
        logger.info(
            "Power: GPU {:.0f}W | Energy {:.3f}Wh",
            state.power_info["gpu_power_watts"],
            state.power_info["energy_wh"],
        )

    headroom = 100 - metrics.frame_budget_percent
    potential = (
        int(metrics.inference_capacity_fps / metrics.camera_fps)
        if metrics.camera_fps > 0
        else 0
    )
    logger.info(
        "Performance: {:.0f}% budget | {:.0f}% headroom | ~{} streams",
        metrics.frame_budget_percent,
        headroom,
        potential,
    )
    logger.info("-" * 60)
