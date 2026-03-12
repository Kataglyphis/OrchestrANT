"""Shared GPU probing utilities for system monitors."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from loguru import logger


try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    pynvml = None  # type: ignore[assignment]
    logger.warning("nvidia-ml-py not available. GPU monitoring disabled.")


@dataclass
class GPUSnapshot:
    """Container for a single GPU metrics reading."""

    utilization: float = 0.0
    memory_used_bytes: int = 0
    memory_total_bytes: int = 0
    temperature_celsius: float = 0.0
    power_watts: float = 0.0


class GPUProbe:
    """Shared GPU probe wrapping pynvml init/read/shutdown."""

    def __init__(self, gpu_index: int = 0) -> None:
        """Initialize pynvml and grab a device handle."""
        self.gpu_index = gpu_index
        self._handle: object | None = None
        self.gpu_name = "N/A"
        self.available = False

        if not PYNVML_AVAILABLE:
            return

        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
            name = pynvml.nvmlDeviceGetName(self._handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            self.gpu_name = name
            self.available = True
            logger.success("GPU monitoring initialized: {}", self.gpu_name)
        except (pynvml.NVMLError, RuntimeError) as exc:
            logger.warning("Failed to initialize GPU monitoring: {}", exc)
            self._handle = None

    def read(self) -> GPUSnapshot | None:
        """Read current GPU metrics. Returns None when unavailable."""
        if not self.available or self._handle is None:
            return None

        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            temp = float(
                pynvml.nvmlDeviceGetTemperature(
                    self._handle, pynvml.NVML_TEMPERATURE_GPU
                )
            )

            power = 0.0
            with suppress(Exception):
                power = pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0

            return GPUSnapshot(
                utilization=float(util.gpu),
                memory_used_bytes=mem.used,
                memory_total_bytes=mem.total,
                temperature_celsius=temp,
                power_watts=power,
            )
        except (pynvml.NVMLError, RuntimeError) as exc:
            logger.debug("Error reading GPU metrics: {}", exc)
            return None

    def shutdown(self) -> None:
        """Release pynvml resources."""
        if PYNVML_AVAILABLE and self.available:
            with suppress(Exception):
                pynvml.nvmlShutdown()
                logger.debug("GPU monitoring shutdown complete")
