"""Vendor-neutral GPU metric types shared by the monitoring backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GPUSnapshot:
    """Container for a single GPU metrics reading."""

    utilization: float = 0.0
    memory_used_bytes: int = 0
    memory_total_bytes: int = 0
    temperature_celsius: float = 0.0
    power_watts: float = 0.0
    vendor: str = "unknown"
