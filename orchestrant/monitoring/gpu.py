"""Vendor-neutral GPU probing shared by the system monitors.

NVIDIA is read through NVML (the optional ``nvidia-ml-py`` extra) and AMD
through :mod:`orchestrant.monitoring.gpu_amd` -- ADL on Windows, amdgpu
sysfs on Linux. :class:`GPUProbe` picks whichever vendor answers and keeps
the historical NVML-facing surface: ``gpu_name``, ``available``,
``read()``, ``shutdown()``.

``PYNVML_AVAILABLE`` is patched by tests and keeps its meaning exactly;
``AMD_AVAILABLE`` is its AMD twin, imported here so the same patch trick
works for either vendor.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Self

from loguru import logger

from orchestrant.monitoring.gpu_amd import AMD_AVAILABLE, AmdGpuProbe
from orchestrant.monitoring.gpu_types import GPUSnapshot


if TYPE_CHECKING:
    from types import TracebackType


__all__ = ["AMD_AVAILABLE", "PYNVML_AVAILABLE", "GPUProbe", "GPUSnapshot"]


try:
    # ty: nvidia-ml-py is an optional extra (see [project.optional-dependencies]
    # gpu/gpu-nvidia). It is absent from the default sync and from every
    # non-NVIDIA machine, which is the whole reason for this guard. A debug
    # line rather than a warning: on AMD hosts this module imports fine and
    # the AMD backend takes over, so "monitoring disabled" was never true.
    import pynvml  # ty: ignore[unresolved-import]
except ImportError:
    pynvml = None  # type: ignore[assignment]
    logger.debug("nvidia-ml-py not installed; NVIDIA GPU monitoring disabled")


# Public, re-exported from orchestrant.monitoring / .pipeline / .yolo, and
# patched by tests/unit/test_system_monitor.py. Derived from the import rather
# than set in both branches so the two can never disagree.
#
# Every call site below ALSO tests `pynvml is not None`, which looks redundant
# and is not: this flag is a plain bool, so it tells a type checker nothing
# about the module object, and `pynvml` is `<module> | None` for the whole file.
# Without the identity test, ty reports "Attribute `nvmlInit` is not defined on
# `None`" on all eleven pynvml uses here - it was right, and only the
# non-gating gate hid it.
PYNVML_AVAILABLE = pynvml is not None


class GPUProbe:
    """Shared GPU probe resolving one NVML or AMD device.

    This class supports both explicit lifecycle management via shutdown()
    and context manager protocol for guaranteed resource cleanup.

    Example:
        >>> with GPUProbe() as gpu:
        ...     snapshot = gpu.read()
        ...     print(f"GPU: {gpu.gpu_name}, Temp: {snapshot.temperature_celsius}C")
    """

    def __init__(self, gpu_index: int = 0, *, vendor: str | None = None) -> None:
        """Probe the requested vendor (or whichever answers) at ``gpu_index``.

        Args:
            gpu_index: GPU device index to monitor (default: 0). AMD devices
                are ordered with the largest dedicated VRAM first.
            vendor: ``"nvidia"`` or ``"amd"`` to skip the other backend;
                ``None`` (default) tries NVIDIA, then AMD.
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
        """Read current GPU metrics.

        Returns:
            GPUSnapshot with current metrics, or None if unavailable.
        """
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
        """Release GPU resources.

        Safe to call multiple times. After shutdown, read() will return None.
        """
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
