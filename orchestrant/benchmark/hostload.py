"""Who burned CPU during a request, measured over the request itself.

The speed runner used to average two instantaneous CPU samples, one taken
before the request was sent and one after it had finished, so neither saw the
inference. A CPU lane pinning 7.5 of 8 cores read as nearly idle. Here the
system counters are read at both ends and differenced (`psutil.cpu_times()`),
which integrates over the whole window instead of sampling around it.

The other half is attribution. `top_cpu_processes()` names the busiest process
by heuristic; `LaneProcess` finds the process actually listening on the lane's
port and sums the CPU-seconds of it and its children over the window. Divided
by the wall time that is `lane_cores`, the number the GenieX page quotes by
hand ("752 % of 800 %", "1.65 cores").

Both only work when the harness shares a host with the server. From WSL2 the
Windows-side geniex.exe is invisible (psutil lists Linux processes, and the
WSL VM's CPU counters are not the host's), so every field here is None there
and the report says why.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _psutil():
    try:
        import psutil

        return psutil
    except Exception:
        return None


def _port(base_url):
    parts = urlsplit(base_url or "")
    if parts.hostname not in LOOPBACK:
        return None
    return parts.port or (443 if parts.scheme == "https" else 80)


class LaneProcess:
    """The local process serving `base_url`, if it runs on this host.

    `available` is False with a `reason` whenever the lane cannot be seen:
    a remote URL, no psutil, or a listener belonging to another OS (WSL2).
    """

    def __init__(self, base_url):
        self.base_url = base_url
        self.proc = None
        self.reason = None
        ps = _psutil()
        port = _port(base_url)
        if ps is None:
            self.reason = "psutil not installed"
            return
        if port is None:
            self.reason = "endpoint is not on this host"
            return
        try:
            for conn in ps.net_connections(kind="tcp"):
                listening = conn.status == ps.CONN_LISTEN and conn.laddr
                if listening and conn.laddr.port == port and conn.pid:
                    self.proc = ps.Process(conn.pid)
                    break
        except Exception as e:  # access denied on some hosts
            self.reason = f"listener lookup failed: {type(e).__name__}"
            return
        if self.proc is None:
            self.reason = f"no local process listens on port {port} (WSL2 cannot see the Windows host's)"

    @property
    def available(self):
        return self.proc is not None

    def info(self):
        """pid, exe and command line: what exactly is serving, with which flags."""
        if self.proc is None:
            return None
        try:
            with self.proc.oneshot():
                return {
                    "pid": self.proc.pid,
                    "exe": self.proc.exe(),
                    "cmdline": self.proc.cmdline(),
                    "started": self.proc.create_time(),
                }
        except Exception as e:
            return {"pid": self.proc.pid, "error": f"{type(e).__name__}: {e}"[:160]}

    def cpu_seconds(self):
        """user+system CPU-seconds of the lane process and all its children.

        None once the lane process itself is gone: a restarted lane read 0.0,
        which made the whole machine's load look like "other" load.
        """
        if self.proc is None:
            return None
        try:
            root = self.proc.cpu_times()
            children = self.proc.children(recursive=True)
        except Exception:  # the lane exited or was restarted
            return None
        total = root.user + root.system
        for p in children:
            try:
                t = p.cpu_times()
                total += t.user + t.system
            except Exception:  # a child that exited mid-read
                continue
        return total


def open_meters(base_url, energy=True):
    """The lane handle and (unless disabled) a started energy meter, announced."""
    from orchestrant.benchmark.energy import EnergyMeter

    lane = LaneProcess(base_url)
    # This host's rails measure this host: for a lane elsewhere they would
    # record the harness's joules under the lane's name.
    meter = EnergyMeter(enabled=energy and lane.available).start()
    if energy and not lane.available:
        meter.reason = f"not metered: {lane.reason}"
    print(
        f"  Lane process: {'pid ' + str(lane.proc.pid) if lane.available else lane.reason}"
    )
    rails = f"EMI rails {', '.join(meter.rails)}" if meter.available else meter.reason
    print(f"  Energy:       {rails}\n")
    return lane, meter


_GPU_KEYS = (
    ("gpu_utilization_percent", 1),
    ("gpu_memory_used_gb", 2),
    ("gpu_power_watts", 1),
)


def avg_optional(before, after, key):
    """Average a sampler metric that may be absent entirely (no GPU): None, not 0."""
    if key not in before or key not in after:
        return None
    return (before[key] + after[key]) / 2


class RequestBracket:
    """Everything measured AROUND one request, so the request loop need not be.

    start() just before sending, stop() the moment the answer is complete,
    fields(completion_tokens) once usage is parsed. `sample` and `top` are
    openai_api's sample_resources and top_cpu_processes, passed in because
    that module imports this one.
    """

    def __init__(self, sample, top, lane=None, meter=None, idle_w=None):
        self._sample, self._top = sample, top
        self._lane, self._meter, self._idle_w = lane, meter, idle_w
        ps = _psutil()
        self._ps_cores = ps.cpu_count() if ps else None

    def start(self):
        self._before = self._sample()
        self._top()  # LB8: prime the per-process counters
        self._window = Window(self._lane).start()
        self._wall0 = time.time()
        return self

    def stop(self):
        self._wall1 = time.time()
        self._load = self._window.stop()
        self._after = self._sample()
        self._busiest = self._top()  # LB8: who actually did the work
        return self

    def fields(self, completion_tokens):
        from orchestrant.benchmark.energy import request_energy

        before, after, load = self._before, self._after, self._load
        # The window integral whenever the lane runs on this host. Otherwise
        # the local counters describe another machine (a remote server, or the
        # WSL VM for a Windows-host lane), and the old snapshot average is kept
        # under a label that says what it is.
        windowed = self._lane is not None and self._lane.available
        if windowed and "cpu_busy_percent_window" in load:
            cpu, method = load["cpu_busy_percent_window"], "window"
        else:
            cpu = (before["cpu_percent"] + after["cpu_percent"]) / 2
            method = "before/after snapshots"
        out = {
            "cpu_percent": round(cpu, 1),
            "cpu_percent_method": method,
            "ram_used_gb": round((before["ram_used_gb"] + after["ram_used_gb"]) / 2, 2),
            "top_processes": self._busiest,
        }
        # GPU fields only when the local probe answered for both samples.
        for key, digits in _GPU_KEYS:
            value = avg_optional(before, after, key)
            if value is not None:
                out[key] = round(value, digits)
        out.update({k: v for k, v in load.items() if k != "cpu_busy_percent_window"})
        # Everything else the machine did during the request. llama.cpp spreads
        # over every core, so a CPU lane loses throughput to each of these: 0.9
        # other cores took one from about 30 to 14 tok/s, while the NPU lane shrugged.
        if method == "window" and "lane_cores" in load and self._ps_cores:
            other = cpu / 100.0 * self._ps_cores - load["lane_cores"]
            out["other_cores"] = round(max(0.0, other), 2)
        out.update(
            request_energy(
                self._meter, self._wall0, self._wall1, completion_tokens, self._idle_w
            )
        )
        if self._idle_w is not None:
            out["cpu_rail_idle_w"] = self._idle_w
        return out


def _mean(values):
    return sum(values) / len(values)


def summary_lines(results):
    """The who-burned-what lines of the speed table's summary."""
    lines = []
    # LB8: the serving process and the inference worker can be different PIDs.
    busiest = {}
    for r in results:
        for proc in r.get("top_processes") or []:
            key = f"{proc['name']} (pid {proc['pid']})"
            busiest[key] = max(busiest.get(key, 0), proc["cpu_percent"])
    if busiest:
        top = sorted(busiest.items(), key=lambda kv: kv[1], reverse=True)[:2]
        lines.append(
            "    Busiest proc:   " + ",  ".join(f"{n} {p:.0f}%" for n, p in top)
        )
    cores = [r["lane_cores"] for r in results if r.get("lane_cores") is not None]
    if cores:
        lines.append(
            f"    Lane CPU:       {_mean(cores):.2f} cores avg  (the serving process tree, over each request)"
        )
    other = [r["other_cores"] for r in results if r.get("other_cores") is not None]
    if other:
        lines.append(
            f"    Other load:     {_mean(other):.2f} cores avg, max {max(other):.2f}  "
            f"(not the lane -- a CPU lane loses throughput to every one)"
        )
    # A ratio of sums, not a mean of ratios: a half-second answer's meter noise
    # spread over 8 tokens must not outweigh an 11 s answer's 256.
    metered = [
        r
        for r in results
        if r.get("cpu_rail_energy_j") is not None and r.get("completion_tokens")
    ]
    if metered:
        tokens = sum(r["completion_tokens"] for r in metered)
        joules = sum(r["cpu_rail_energy_j"] for r in metered)
        seconds = sum(
            r.get("cpu_rail_window_s") or r.get("latency_s") or 0 for r in metered
        )
        line = f"    CPU-rail energy: {joules / tokens:.3f} J/token gross"
        if all("cpu_rail_net_energy_j" in r for r in metered):
            net = sum(r["cpu_rail_net_energy_j"] for r in metered)
            line += f", {net / tokens:.3f} net of idle"
        line += f"  ({joules / seconds:.1f} W avg)" if seconds else ""
        lines.append(line + "  -- CPU clusters only; NPU/GPU draw is not metered")
    return lines


class Window:
    """CPU accounting over one request: start() before sending, stop() after."""

    def __init__(self, lane=None):
        self.lane = lane
        self._ps = _psutil()
        self._t0 = self._sys0 = self._lane0 = None
        self.result = {}

    def start(self):
        self._t0 = time.monotonic()
        self._sys0 = self._ps.cpu_times() if self._ps else None
        self._lane0 = self.lane.cpu_seconds() if self.lane else None
        return self

    def stop(self):
        wall = time.monotonic() - self._t0
        out = {}
        if self._ps is not None and self._sys0 is not None:
            sys1 = self._ps.cpu_times()
            busy = sum(sys1) - sys1.idle - (sum(self._sys0) - self._sys0.idle)
            total = sum(sys1) - sum(self._sys0)
            if total > 0:
                out["cpu_busy_percent_window"] = round(100.0 * busy / total, 1)
        if self._lane0 is not None:
            lane1 = self.lane.cpu_seconds()
            if lane1 is not None and wall > 0:
                used = max(0.0, lane1 - self._lane0)
                out["lane_cpu_s"] = round(used, 2)
                out["lane_cores"] = round(used / wall, 2)
        self.result = out
        return out
