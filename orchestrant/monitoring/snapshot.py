"""Single-read system snapshot shared by both SystemMonitor variants.

Two monitors consume this: :class:`orchestrant.monitoring.system.SystemMonitor`
(a time-series logger with start/record/summary) and
:class:`orchestrant.pipeline.monitoring.system.SystemMonitor` (a live
per-frame snapshotter for pipeline UIs). They shape the data differently but
must read it identically — this module owns that read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psutil


if TYPE_CHECKING:
    from orchestrant.monitoring.gpu import GPUProbe, GPUSnapshot


@dataclass(frozen=True)
class RawSystemSnapshot:
    """One synchronized read of CPU, RAM, and (optionally) GPU state."""

    cpu_percent: float
    ram: Any  # psutil svmem namedtuple (total/used/available/percent)
    gpu: GPUSnapshot | None


def read_raw_snapshot(gpu_probe: GPUProbe) -> RawSystemSnapshot:
    """Read CPU utilization, virtual memory, and the GPU probe in one pass.

    ``psutil.cpu_percent(interval=None)`` is non-blocking and relative to the
    previous call, so callers must have primed it once at construction time.
    """
    return RawSystemSnapshot(
        cpu_percent=psutil.cpu_percent(interval=None),
        ram=psutil.virtual_memory(),
        gpu=gpu_probe.read(),
    )
