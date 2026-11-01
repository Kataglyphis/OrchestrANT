"""Host and accelerator identity recorded with every speed run, and the busiest processes during a request."""

import os
from datetime import UTC, datetime


def collect_gpu_info():
    """Name the accelerator that served the endpoint, if one is readable."""
    try:
        from orchestrant.monitoring.gpu import GPUProbe

        with GPUProbe() as probe:
            if not probe.available:
                return {}
            snapshot = probe.read()
            gpu = {
                "vendor": probe.vendor,
                "name": probe.gpu_name,
                "backend": probe.backend,
            }
            if probe.memory_type:
                gpu["memory_type"] = probe.memory_type
            if probe.memory_bandwidth_mbps:
                gpu["memory_bandwidth_gbps"] = round(
                    probe.memory_bandwidth_mbps / 1000, 1
                )
            if snapshot is not None:
                gpu["memory_total_mb"] = round(snapshot.memory_total_bytes / (1024**2))
                gpu["memory_used_mb"] = round(snapshot.memory_used_bytes / (1024**2))
            return gpu
    except Exception:
        return {}


def _os_info(info):
    """Platform identity from the standard library."""
    try:
        import platform

        info["os"] = platform.system()
        info["os_release"] = platform.release()
        info["os_version"] = platform.version()
        info["architecture"] = platform.machine()
        info["processor"] = platform.processor()
    except Exception:  # nosec B110 -- best-effort probe
        pass


def _first_cpu_field(cpus, needle):
    """The value of the first `needle` line of the first CPU block, else None."""
    for line in cpus[0].split("\n") if cpus else []:
        if needle in line.lower():
            return line.split(":")[1].strip()
    return None


def _cpu_info(info):
    """CPU model, cores and threads from /proc/cpuinfo (Linux)."""
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
        cpus = [p for p in cpuinfo.split("\n\n") if p.strip()]
        info["cpu_count"] = len(cpus)
        model = _first_cpu_field(cpus, "model name")
        if model is not None:
            info["cpu_model"] = model
        core_ids = {
            line.split(":")[1].strip()
            for cpu in cpus
            for line in cpu.split("\n")
            if "core id" in line.lower()
        }
        if core_ids:
            info["cpu_physical_cores"] = len(core_ids)
        siblings = _first_cpu_field(cpus, "siblings")
        if siblings is not None:
            info["cpu_threads_per_core"] = int(siblings)
        info["cpu_total_threads"] = info["cpu_count"]
    except Exception:  # nosec B110 -- best-effort probe
        pass


def _ram_and_container_info(info):
    """Total RAM from /proc/meminfo and container membership from /proc/1/cgroup."""
    try:
        with open("/proc/meminfo") as f:
            mem = f.read()
        for line in mem.split("\n"):
            if line.startswith("MemTotal:"):
                info["ram_total_kb"] = int(line.split()[1])
                info["ram_total_gb"] = round(info["ram_total_kb"] / (1024**2), 1)
                break
    except Exception:  # nosec B110 -- best-effort probe
        pass

    try:
        with open("/proc/1/cgroup") as f:
            cgroup = f.read()
        info["in_container"] = (
            "docker" in cgroup or "containerd" in cgroup or len(cgroup.splitlines()) > 0
        )
    except Exception:
        info["in_container"] = None


def _portable_fallback(info):
    """What /proc could not provide (all of it on Windows and macOS)."""
    try:
        import platform as _pf

        info.setdefault("os", _pf.system())
        info.setdefault("os_release", _pf.release())
        info.setdefault("architecture", _pf.machine())
        if not info.get("processor"):
            info["processor"] = _pf.processor() or None
    except Exception:  # nosec B110 -- best-effort probe
        pass
    try:
        import psutil as _ps

        if not info.get("cpu_physical_cores"):
            info["cpu_physical_cores"] = _ps.cpu_count(logical=False)
        if not info.get("cpu_total_threads"):
            info["cpu_total_threads"] = _ps.cpu_count(logical=True)
        if not info.get("ram_total_gb"):
            info["ram_total_gb"] = round(_ps.virtual_memory().total / (1024**3), 1)
    except Exception:  # nosec B110 -- best-effort probe
        pass


def collect_hardware_info():
    """Collect system hardware info for reproducibility, naming gaps in `incomplete`."""
    info = {}
    info["timestamp"] = datetime.now(UTC).isoformat()
    _os_info(info)
    _cpu_info(info)
    _ram_and_container_info(info)
    info["ollama_host"] = os.environ.get("OLLAMA_HOST", "default")

    gpu = collect_gpu_info()
    if gpu:
        info["gpu"] = gpu

    _portable_fallback(info)

    # Name the gaps: a benchmark whose host is unknown cannot be compared later.
    expected = ("os", "architecture", "cpu_total_threads", "ram_total_gb")
    missing = [k for k in expected if not info.get(k)]
    if missing:
        info["incomplete"] = missing

    return info


def top_cpu_processes(limit=3):
    """Busiest processes since the PREVIOUS call: the port owner may not be the worker."""
    try:
        import psutil
    except Exception:
        return []
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            pct = p.cpu_percent(None)
            # pid 0 is Windows' "System Idle Process": 737 % of idle ranked first.
            if pct > 0 and p.pid != 0:
                procs.append(
                    {
                        "pid": p.pid,
                        "name": p.info.get("name") or "?",
                        "cpu_percent": round(pct, 1),
                    }
                )
        except Exception:  # nosec B112 -- skips a process that ended mid-read
            continue
    procs.sort(key=lambda d: d["cpu_percent"], reverse=True)
    return procs[:limit]
