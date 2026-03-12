"""System resource monitoring helpers."""

from __future__ import annotations

import psutil
from loguru import logger

from orchestr_ant_ion.monitoring.gpu import GPUProbe
from orchestr_ant_ion.pipeline.types import SystemStats


class SystemMonitor:
    """Monitor system resources including CPU, RAM, and GPU."""

    def __init__(self, gpu_device_id: int = 0) -> None:
        """Initialize system monitoring and optional GPU probing."""
        self._gpu = GPUProbe(gpu_device_id)

        # Expose convenience attributes for backward compatibility
        self.gpu_available = self._gpu.available
        self.gpu_name = self._gpu.gpu_name

        psutil.cpu_percent(interval=None)
        self.process = psutil.Process()
        self.process.cpu_percent()

    @property
    def gpu_handle(self) -> object | None:
        """Return the GPU handle if initialized."""
        return self._gpu._handle  # noqa: SLF001

    def get_stats(self) -> SystemStats:
        """Collect current system-wide CPU, RAM, and GPU statistics."""
        stats = SystemStats()

        stats.cpu_percent = psutil.cpu_percent(interval=None)

        ram = psutil.virtual_memory()
        stats.ram_percent = ram.percent
        stats.ram_used_gb = ram.used / (1024**3)
        stats.ram_total_gb = ram.total / (1024**3)

        snapshot = self._gpu.read()
        if snapshot is not None:
            stats.gpu_percent = snapshot.utilization
            stats.gpu_memory_used_gb = snapshot.memory_used_bytes / (1024**3)
            stats.gpu_memory_total_gb = snapshot.memory_total_bytes / (1024**3)
            stats.gpu_memory_percent = (
                (snapshot.memory_used_bytes / snapshot.memory_total_bytes) * 100
                if snapshot.memory_total_bytes > 0
                else 0
            )
            stats.gpu_temp_celsius = snapshot.temperature_celsius
            stats.gpu_power_watts = snapshot.power_watts
            stats.gpu_name = self._gpu.gpu_name

        return stats

    def get_process_stats(self) -> dict:
        """Collect current process CPU/RAM/thread statistics."""
        try:
            return {
                # psutil returns process CPU% relative to a single logical core;
                # values can exceed 100% on multi-core systems.
                "cpu_percent": self.process.cpu_percent(),
                "memory_mb": self.process.memory_info().rss / (1024**2),
                "threads": self.process.num_threads(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.debug("Failed to read process stats: {}", exc)
            return {"cpu_percent": 0, "memory_mb": 0, "threads": 0}

    def shutdown(self) -> None:
        """Shutdown GPU monitoring resources, if available."""
        self._gpu.shutdown()
