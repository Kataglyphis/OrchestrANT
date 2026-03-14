"""System monitoring module for tracking CPU, GPU, and memory metrics."""

from __future__ import annotations

import time
from dataclasses import dataclass

import psutil
from loguru import logger

from orchestr_ant_ion.monitoring.gpu import GPUProbe


@dataclass
class SystemMetrics:
    """Container for system metrics at a specific timestamp."""

    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    gpu_utilization: float | None = None
    gpu_memory_used_mb: float | None = None
    gpu_memory_total_mb: float | None = None
    gpu_temperature: float | None = None


class SystemMonitor:
    """Monitor system resources (CPU, RAM, GPU) and log metrics over time.

    Example:
        >>> monitor = SystemMonitor(interval=1.0)
        >>> monitor.start()
        >>> # ... do some work ...
        >>> monitor.stop()
        >>> metrics = monitor.get_metrics()
        >>> monitor.print_summary()
    """

    def __init__(self, interval: float = 1.0, gpu_index: int = 0) -> None:
        """Initialize the system monitor.

        Args:
            interval: Time interval between measurements in seconds
            gpu_index: GPU device index to monitor (default: 0)
        """
        self.interval = interval
        self.gpu_index = gpu_index
        self.metrics: list[SystemMetrics] = []
        self._monitoring = False
        self._gpu = GPUProbe(gpu_index)

        # Prime psutil's cpu_percent for non-blocking reads
        psutil.cpu_percent(interval=None)

    def _collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()

        gpu_util = None
        gpu_mem_used = None
        gpu_mem_total = None
        gpu_temp = None

        snapshot = self._gpu.read()
        if snapshot is not None:
            gpu_util = snapshot.utilization
            gpu_mem_used = snapshot.memory_used_bytes / (1024**2)
            gpu_mem_total = snapshot.memory_total_bytes / (1024**2)
            gpu_temp = snapshot.temperature_celsius

        return SystemMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            memory_used_mb=memory.used / (1024**2),
            memory_available_mb=memory.available / (1024**2),
            gpu_utilization=gpu_util,
            gpu_memory_used_mb=gpu_mem_used,
            gpu_memory_total_mb=gpu_mem_total,
            gpu_temperature=gpu_temp,
        )

    def start(self) -> None:
        """Start monitoring and collecting metrics."""
        self._monitoring = True
        logger.info("System monitoring started (interval: {}s)", self.interval)
        self.metrics = []

    def record(self) -> None:
        """Record a single metric snapshot."""
        if not self._monitoring:
            logger.warning("Monitor not started. Call start() first.")
            return

        metrics = self._collect_metrics()
        self.metrics.append(metrics)

        log_msg = f"CPU: {metrics.cpu_percent:.1f}% | RAM: {metrics.memory_percent:.1f}% ({metrics.memory_used_mb:.0f}MB)"

        if metrics.gpu_utilization is not None:
            log_msg += f" | GPU: {metrics.gpu_utilization:.1f}% | VRAM: {metrics.gpu_memory_used_mb:.0f}/{metrics.gpu_memory_total_mb:.0f}MB | Temp: {metrics.gpu_temperature:.0f}C"

        logger.debug(log_msg)

    def stop(self) -> None:
        """Stop monitoring."""
        self._monitoring = False
        logger.info(
            "System monitoring stopped. Collected {} samples.", len(self.metrics)
        )

    def get_metrics(self) -> list[SystemMetrics]:
        """Get all collected metrics."""
        return self.metrics

    @property
    def is_monitoring(self) -> bool:
        """Return True if monitoring is active."""
        return self._monitoring

    @property
    def gpu_handle(self) -> object | None:
        """Return the GPU handle if initialized."""
        return self._gpu._handle  # noqa: SLF001

    def print_summary(self) -> None:
        """Print a summary of collected metrics."""
        if not self.metrics:
            logger.warning("No metrics collected.")
            return

        cpu_values = [m.cpu_percent for m in self.metrics]
        mem_values = [m.memory_percent for m in self.metrics]

        cpu_count = len(cpu_values)
        cpu_avg = sum(cpu_values) / cpu_count
        cpu_min = min(cpu_values)
        cpu_max = max(cpu_values)

        mem_count = len(mem_values)
        mem_avg = sum(mem_values) / mem_count
        mem_min = min(mem_values)
        mem_max = max(mem_values)

        logger.info("=" * 60)
        logger.info("SYSTEM MONITORING SUMMARY")
        logger.info("=" * 60)
        logger.info("Samples collected: {}", len(self.metrics))
        logger.info(
            "Duration: {:.2f}s",
            self.metrics[-1].timestamp - self.metrics[0].timestamp,
        )
        logger.info("")
        logger.info("CPU Usage:")
        logger.info("  Average: {:.1f}%", cpu_avg)
        logger.info("  Min: {:.1f}%", cpu_min)
        logger.info("  Max: {:.1f}%", cpu_max)
        logger.info("")
        logger.info("Memory Usage:")
        logger.info("  Average: {:.1f}%", mem_avg)
        logger.info("  Min: {:.1f}%", mem_min)
        logger.info("  Max: {:.1f}%", mem_max)

        if self.metrics[0].gpu_utilization is not None:
            gpu_util_values = [
                m.gpu_utilization for m in self.metrics if m.gpu_utilization is not None
            ]
            gpu_mem_values = [
                m.gpu_memory_used_mb
                for m in self.metrics
                if m.gpu_memory_used_mb is not None
            ]
            gpu_temp_values = [
                m.gpu_temperature for m in self.metrics if m.gpu_temperature is not None
            ]

            if gpu_util_values:
                gpu_util_count = len(gpu_util_values)
                logger.info("")
                logger.info("GPU Usage:")
                logger.info(
                    "  Average: {:.1f}%",
                    sum(gpu_util_values) / gpu_util_count,
                )
                logger.info("  Min: {:.1f}%", min(gpu_util_values))
                logger.info("  Max: {:.1f}%", max(gpu_util_values))

            if gpu_mem_values:
                gpu_mem_count = len(gpu_mem_values)
                logger.info("")
                logger.info("GPU Memory:")
                logger.info(
                    "  Average: {:.0f}MB",
                    sum(gpu_mem_values) / gpu_mem_count,
                )
                logger.info("  Min: {:.0f}MB", min(gpu_mem_values))
                logger.info("  Max: {:.0f}MB", max(gpu_mem_values))

            if gpu_temp_values:
                gpu_temp_count = len(gpu_temp_values)
                logger.info("")
                logger.info("GPU Temperature:")
                logger.info(
                    "  Average: {:.1f}C",
                    sum(gpu_temp_values) / gpu_temp_count,
                )
                logger.info("  Min: {:.1f}C", min(gpu_temp_values))
                logger.info("  Max: {:.1f}C", max(gpu_temp_values))

        logger.info("=" * 60)

    def __del__(self) -> None:
        """Cleanup GPU monitoring on deletion."""
        self._gpu.shutdown()
