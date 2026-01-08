"""System monitoring module for tracking CPU, GPU, and memory metrics."""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

import psutil
from loguru import logger

try:
    import pynvml

    NVIDIA_AVAILABLE = True
except ImportError:
    NVIDIA_AVAILABLE = False
    logger.warning("nvidia-ml-py not available. GPU monitoring disabled.")


@dataclass
class SystemMetrics:
    """Container for system metrics at a specific timestamp."""

    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    gpu_utilization: Optional[float] = None
    gpu_memory_used_mb: Optional[float] = None
    gpu_memory_total_mb: Optional[float] = None
    gpu_temperature: Optional[float] = None


class SystemMonitor:
    """
    Monitor system resources (CPU, RAM, GPU) and log metrics over time.

    Example:
        >>> monitor = SystemMonitor(interval=1.0)
        >>> monitor.start()
        >>> # ... do some work ...
        >>> monitor.stop()
        >>> metrics = monitor.get_metrics()
        >>> monitor.print_summary()
    """

    def __init__(self, interval: float = 1.0, gpu_index: int = 0):
        """
        Initialize the system monitor.

        Args:
            interval: Time interval between measurements in seconds
            gpu_index: GPU device index to monitor (default: 0)
        """
        self.interval = interval
        self.gpu_index = gpu_index
        self.metrics: List[SystemMetrics] = []
        self._monitoring = False
        self._gpu_handle = None

        # Initialize NVIDIA GPU monitoring if available
        if NVIDIA_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
                gpu_name = pynvml.nvmlDeviceGetName(self._gpu_handle)
                logger.info(f"GPU monitoring initialized for: {gpu_name}")
            except Exception as e:
                logger.warning(f"Failed to initialize GPU monitoring: {e}")
                self._gpu_handle = None

    def _collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        # CPU and memory metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()

        # GPU metrics
        gpu_util = None
        gpu_mem_used = None
        gpu_mem_total = None
        gpu_temp = None

        if self._gpu_handle:
            try:
                # GPU utilization
                utilization = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                gpu_util = float(utilization.gpu)

                # GPU memory
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                gpu_mem_used = mem_info.used / (1024**2)  # Convert to MB
                gpu_mem_total = mem_info.total / (1024**2)

                # GPU temperature
                gpu_temp = float(
                    pynvml.nvmlDeviceGetTemperature(
                        self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                    )
                )
            except Exception as e:
                logger.debug(f"Error reading GPU metrics: {e}")

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

    def start(self):
        """Start monitoring and collecting metrics."""
        self._monitoring = True
        logger.info(f"System monitoring started (interval: {self.interval}s)")
        self.metrics = []

    def record(self):
        """Record a single metric snapshot."""
        if not self._monitoring:
            logger.warning("Monitor not started. Call start() first.")
            return

        metrics = self._collect_metrics()
        self.metrics.append(metrics)

        log_msg = (
            f"CPU: {metrics.cpu_percent:.1f}% | "
            f"RAM: {metrics.memory_percent:.1f}% ({metrics.memory_used_mb:.0f}MB)"
        )

        if metrics.gpu_utilization is not None:
            log_msg += (
                f" | GPU: {metrics.gpu_utilization:.1f}% | "
                f"VRAM: {metrics.gpu_memory_used_mb:.0f}/{metrics.gpu_memory_total_mb:.0f}MB | "
                f"Temp: {metrics.gpu_temperature:.0f}°C"
            )

        logger.debug(log_msg)

    def stop(self):
        """Stop monitoring."""
        self._monitoring = False
        logger.info(
            f"System monitoring stopped. Collected {len(self.metrics)} samples."
        )

    def get_metrics(self) -> List[SystemMetrics]:
        """Get all collected metrics."""
        return self.metrics

    def print_summary(self):
        """Print a summary of collected metrics."""
        if not self.metrics:
            logger.warning("No metrics collected.")
            return

        cpu_values = [m.cpu_percent for m in self.metrics]
        mem_values = [m.memory_percent for m in self.metrics]

        logger.info("=" * 60)
        logger.info("SYSTEM MONITORING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Samples collected: {len(self.metrics)}")
        logger.info(
            f"Duration: {self.metrics[-1].timestamp - self.metrics[0].timestamp:.2f}s"
        )
        logger.info("")
        logger.info(f"CPU Usage:")
        logger.info(f"  Average: {sum(cpu_values) / len(cpu_values):.1f}%")
        logger.info(f"  Min: {min(cpu_values):.1f}%")
        logger.info(f"  Max: {max(cpu_values):.1f}%")
        logger.info("")
        logger.info(f"Memory Usage:")
        logger.info(f"  Average: {sum(mem_values) / len(mem_values):.1f}%")
        logger.info(f"  Min: {min(mem_values):.1f}%")
        logger.info(f"  Max: {max(mem_values):.1f}%")

        if self.metrics[0].gpu_utilization is not None:
            gpu_util_values = [
                m.gpu_utilization for m in self.metrics if m.gpu_utilization
            ]
            gpu_mem_values = [
                m.gpu_memory_used_mb for m in self.metrics if m.gpu_memory_used_mb
            ]
            gpu_temp_values = [
                m.gpu_temperature for m in self.metrics if m.gpu_temperature
            ]

            if gpu_util_values:
                logger.info("")
                logger.info(f"GPU Usage:")
                logger.info(
                    f"  Average: {sum(gpu_util_values) / len(gpu_util_values):.1f}%"
                )
                logger.info(f"  Min: {min(gpu_util_values):.1f}%")
                logger.info(f"  Max: {max(gpu_util_values):.1f}%")

            if gpu_mem_values:
                logger.info("")
                logger.info(f"GPU Memory:")
                logger.info(
                    f"  Average: {sum(gpu_mem_values) / len(gpu_mem_values):.0f}MB"
                )
                logger.info(f"  Min: {min(gpu_mem_values):.0f}MB")
                logger.info(f"  Max: {max(gpu_mem_values):.0f}MB")

            if gpu_temp_values:
                logger.info("")
                logger.info(f"GPU Temperature:")
                logger.info(
                    f"  Average: {sum(gpu_temp_values) / len(gpu_temp_values):.1f}°C"
                )
                logger.info(f"  Min: {min(gpu_temp_values):.1f}°C")
                logger.info(f"  Max: {max(gpu_temp_values):.1f}°C")

        logger.info("=" * 60)

    def __del__(self):
        """Cleanup GPU monitoring on deletion."""
        if NVIDIA_AVAILABLE and self._gpu_handle:
            try:
                pynvml.nvmlShutdown()
            except:
                pass
