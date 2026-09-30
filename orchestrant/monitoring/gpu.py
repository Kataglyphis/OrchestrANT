"""Vendor-neutral GPU probing (NVML for NVIDIA, gpu_amd for AMD) for the system monitors."""

from __future__ import annotations

import importlib
from contextlib import suppress
from typing import TYPE_CHECKING, Self

from loguru import logger

from orchestrant.monitoring.gpu_amd import AMD_AVAILABLE, AmdGpuProbe
from orchestrant.monitoring.gpu_types import GPUSnapshot


if TYPE_CHECKING:
    from types import TracebackType


__all__ = ["AMD_AVAILABLE", "PYNVML_AVAILABLE", "GPUProbe", "GPUSnapshot"]


try:
    # Optional extra; debug, not warning, below: on AMD hosts the AMD backend takes over.
    pynvml = importlib.import_module("pynvml")
except ImportError:
    # importlib above, not `import`: ty would type pynvml as the module and reject None.
    pynvml = None
    logger.debug("nvidia-ml-py not installed; NVIDIA GPU monitoring disabled")


# Patched by tests; call sites still test `pynvml is not None`, since a bool narrows nothing.
PYNVML_AVAILABLE = pynvml is not None


class GPUProbe:
    """Shared GPU probe resolving one NVML or AMD device; also a context manager."""

    def __init__(self, gpu_index: int = 0, *, vendor: str | None = None) -> None:
        """Probe the requested vendor (or whichever answers) at ``gpu_index``.

        Args:
            gpu_index: AMD devices are ordered largest dedicated VRAM first.
            vendor: ``"nvidia"`` or ``"amd"``; ``None`` tries NVIDIA, then AMD.
        """
        self.gpu_index = gpu_index
        self.vendor = "none"
        self.backend = "none"
        self._handle: object | None = None
        self.gpu_name = "N/A"
        self.memory_type = ""
        self.memory_bandwidth_mbps = 0
        self.available = False
        self._amd: AmdGpuProbe | None = None

        if vendor in (None, "nvidia") and PYNVML_AVAILABLE and pynvml is not None:
            self._init_nvidia()
        if not self.available and vendor in (None, "amd") and AMD_AVAILABLE:
            self._init_amd()
        if not self.available:
            logger.debug("No GPU backend answered for device index {}", gpu_index)

    def _init_nvidia(self) -> None:
        """Initialize the NVML device handle for ``gpu_index``."""
        if pynvml is None:  # ty needs the identity test in this frame too
            return
        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.gpu_index)
            name = pynvml.nvmlDeviceGetName(self._handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            self.gpu_name = name
            self.vendor = "nvidia"
            self.backend = "nvml"
            self.available = True
            logger.success("GPU monitoring initialized: {}", self.gpu_name)
        except (pynvml.NVMLError, RuntimeError) as exc:
            logger.debug("NVIDIA GPU monitoring unavailable: {}", exc)
            self._handle = None

    def _init_amd(self) -> None:
        """Initialize the platform AMD backend for ``gpu_index``."""
        try:
            probe = AmdGpuProbe(self.gpu_index)
        except Exception as exc:  # pragma: no cover - defensive: driver/DLL quirks
            logger.debug("AMD GPU monitoring unavailable: {}", exc)
            return
        if not probe.available:
            return
        self._amd = probe
        self.vendor = "amd"
        self.backend = probe.backend
        self.gpu_name = probe.name
        self.memory_type = probe.memory_type
        self.memory_bandwidth_mbps = probe.memory_bandwidth_mbps
        self._handle = probe.handle
        self.available = True

    def read(self) -> GPUSnapshot | None:
        """Read current GPU metrics, or None if unavailable."""
        if self._amd is not None:
            return self._amd.read()
        if not self.available or self._handle is None or pynvml is None:
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
                memory_used_bytes=int(mem.used),
                memory_total_bytes=int(mem.total),
                temperature_celsius=temp,
                power_watts=power,
                vendor="nvidia",
            )
        except (pynvml.NVMLError, RuntimeError) as exc:
            logger.debug("Error reading GPU metrics: {}", exc)
            return None

    def shutdown(self) -> None:
        """Release GPU resources; idempotent, and read() returns None afterwards."""
        if self._amd is not None:
            self._amd.shutdown()
            self._amd = None
            self._handle = None
            self.available = False
            return
        if (
            self._handle is not None
            and PYNVML_AVAILABLE
            and self.available
            and pynvml is not None
        ):
            with suppress(Exception):
                pynvml.nvmlShutdown()
                logger.debug("GPU monitoring shutdown complete")
            self._handle = None
            self.available = False

    def __enter__(self) -> Self:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager and ensure cleanup."""
        self.shutdown()
