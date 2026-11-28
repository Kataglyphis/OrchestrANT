"""The CPUs the thread rules count: the affinity mask, capped by every cgroup v2 quota up to the root (hub CON304)."""

from __future__ import annotations

import math
import os
from fractions import Fraction
from pathlib import Path


CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_CGROUP = Path("/proc/self/cgroup")


def usable_of(affinity: int, cpu_max: list[str | None]) -> int:
    """max(1, floor(min(affinity, the smallest quota/period of cpu_max))); None and ``max`` set no limit."""
    limit = Fraction(affinity)
    for text in cpu_max:
        if text is None:
            continue
        fields = text.split()
        if len(fields) != 2 or fields[0] == "max":
            continue
        quota, period = int(fields[0]), int(fields[1])
        if period > 0:
            limit = min(limit, Fraction(quota, period))
    return max(1, math.floor(limit))


def cgroup_cpu_max(
    root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_CGROUP
) -> list[str | None]:
    """Each cpu.max from the process's cgroup v2 up to the root, own first, None where it is missing."""
    try:
        lines = proc_cgroup.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    path = next((line[3:] for line in lines if line.startswith("0::")), None)
    if path is None:
        return []
    parts = [part for part in path.split("/") if part]
    # A cgroup outside this namespace shows as ../..; only the namespace's root is visible then.
    if ".." in parts:
        parts = []
    found: list[str | None] = []
    for depth in range(len(parts), -1, -1):
        try:
            found.append(
                root.joinpath(*parts[:depth], "cpu.max")
                .read_text(encoding="utf-8")
                .strip()
            )
        except OSError:
            found.append(None)
    return found


def affinity_count() -> int:
    """The CPUs of the affinity mask, or the machine's where the platform has none."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def usable_cpus() -> int:
    """The usable CPUs of this process, at least 1."""
    return usable_of(affinity_count(), cgroup_cpu_max())
