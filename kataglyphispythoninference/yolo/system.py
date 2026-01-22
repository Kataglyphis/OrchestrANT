from __future__ import annotations

import psutil
from loguru import logger

from kataglyphispythoninference.yolo.types import SystemStats


try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("pynvml not available - GPU monitoring disabled")


class SystemMonitor:
    """Monitor system resources including CPU, RAM, and GPU."""

    def __init__(self, gpu_device_id: int = 0) -> None:
        self.gpu_device_id = gpu_device_id
        self.gpu_handle = None
        self.gpu_available = False
        self.gpu_name = "N/A"

        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_device_id)
                gpu_name = pynvml.nvmlDeviceGetName(self.gpu_handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode("utf-8")
                self.gpu_name = gpu_name
                self.gpu_available = True
                logger.success("GPU monitoring initialized: {}", self.gpu_name)
            except Exception as exc:
                logger.warning("Failed to initialize GPU monitoring: {}", exc)

        psutil.cpu_percent(interval=None)
        self.process = psutil.Process()
        self.process.cpu_percent()

    def get_stats(self) -> SystemStats:
        stats = SystemStats()

        stats.cpu_percent = psutil.cpu_percent(interval=None)

        ram = psutil.virtual_memory()
        stats.ram_percent = ram.percent
        stats.ram_used_gb = ram.used / (1024**3)
        stats.ram_total_gb = ram.total / (1024**3)

        if self.gpu_available and self.gpu_handle:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                stats.gpu_percent = util.gpu

                mem = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                stats.gpu_memory_used_gb = mem.used / (1024**3)
                stats.gpu_memory_total_gb = mem.total / (1024**3)
                stats.gpu_memory_percent = (
                    (mem.used / mem.total) * 100 if mem.total > 0 else 0
                )

                stats.gpu_temp_celsius = pynvml.nvmlDeviceGetTemperature(
                    self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )

                try:
                    power = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle)
                    stats.gpu_power_watts = power / 1000.0
                except Exception:
                    stats.gpu_power_watts = 0.0

                stats.gpu_name = self.gpu_name

            except Exception as exc:
                logger.warning("Error reading GPU stats: {}", exc)

        return stats

    def get_process_stats(self) -> dict:
        try:
            return {
                "cpu_percent": self.process.cpu_percent(),
                "memory_mb": self.process.memory_info().rss / (1024**2),
                "threads": self.process.num_threads(),
            }
        except Exception:
            return {"cpu_percent": 0, "memory_mb": 0, "threads": 0}

    def shutdown(self) -> None:
        if PYNVML_AVAILABLE and self.gpu_available:
            try:
                pynvml.nvmlShutdown()
                logger.debug("GPU monitoring shutdown complete")
            except Exception:
                pass
