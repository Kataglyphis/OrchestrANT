"""Who burned CPU during a request, measured over the request itself."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from orchestrant.benchmark import winhost


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

    `listens_here` is None when the lookup never ran; a hidden-pid listener is still local.
    """

    def __init__(self, base_url):
        self.base_url = base_url
        self.proc = None
        self.reason = None
        self.listens_here = None
        ps = _psutil()
        port = _port(base_url)
        if ps is None:
            self.reason = "psutil not installed"
            return
        if port is None:
            self.reason = "endpoint is not on this host"
            return
        try:
            self._find_listener(ps, port)
        except Exception as e:  # access denied on some hosts
            self.listens_here = None
            self.reason = f"listener lookup failed: {type(e).__name__}"
            return
        if self.listens_here and self.proc is None:
            self.reason = (
                f"a process listens on port {port} here, but its pid is hidden"
            )
        elif self.proc is None:
            self.reason = f"no local process listens on port {port} (WSL2 cannot see the Windows host's)"

    def _find_listener(self, ps, port):
        self.listens_here = False
        for conn in ps.net_connections(kind="tcp"):
            listening = conn.status == ps.CONN_LISTEN and conn.laddr
            if listening and conn.laddr.port == port:
                self.listens_here = True
                if conn.pid:
                    self.proc = ps.Process(conn.pid)
                    return

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
        """user+system CPU-seconds of the lane and its children; None once the lane is gone."""
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
            except Exception:  # nosec B112 -- a child that exited mid-read
                continue
        return total


def open_meters(base_url, energy=True):
    """The lane handle and (unless disabled) a started energy meter, announced."""
    from orchestrant.benchmark.energy import EnergyMeter

    lane = LaneProcess(base_url)
    # This host's rails measure this host, not a lane elsewhere.
    meter = EnergyMeter(enabled=energy and lane.available).start()
    if energy and not lane.available:
        meter.reason = f"not metered: {lane.reason}"
    print(
        f"  Lane process: {'pid ' + str(lane.proc.pid) if lane.proc is not None else lane.reason}"
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
    """Everything measured AROUND one request: start(), stop(), then fields(completion_tokens)."""

    def __init__(self, sample, top, lane=None, meter=None, idle_w=None):
        self._sample, self._top = sample, top
        self._lane, self._meter, self._idle_w = lane, meter, idle_w
        ps = _psutil()
        self._ps_cores = ps.cpu_count() if ps else None

    def start(self):
        self._before = self._sample()
        self._top()  # prime the per-process counters
        self._window = Window(self._lane).start()
        self._wall0 = time.time()
        return self

    def stop(self):
        self._wall1 = time.time()
        self._load = self._window.stop()
        self._after = self._sample()
        self._busiest = self._top()  # who actually did the work
        return self

    def fields(self, completion_tokens):
        from orchestrant.benchmark.energy import request_energy

        before, after, load = self._before, self._after, self._load
        # Integrate only when the lane runs here; otherwise the counters describe another machine.
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
        # Everything else the machine did: llama.cpp spreads over every core, so each one costs.
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


def _window_s(row):
    """Seconds a row's joules cover; older rows fall back to the request's latency."""
    return row.get("cpu_rail_window_s") or row.get("latency_s") or 0


def _mean(values):
    return sum(values) / len(values)


def summary_lines(results):
    """The who-burned-what lines of the speed table's summary."""
    lines = []
    # The serving process and the inference worker can be different PIDs.
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
    # A ratio of sums: a short answer's meter noise must not outweigh a long one's.
    metered = [
        r
        for r in results
        if r.get("cpu_rail_energy_j") is not None and r.get("completion_tokens")
    ]
    if metered:
        tokens = sum(r["completion_tokens"] for r in metered)
        joules = sum(r["cpu_rail_energy_j"] for r in metered)
        seconds = sum(map(_window_s, metered))
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
        self._t0 = 0.0
        self._sys0 = self._lane0 = None
        self.wall = 0.0
        self.result = {}

    def start(self):
        self._t0 = time.monotonic()
        self._sys0 = self._ps.cpu_times() if self._ps else None
        self._lane0 = self.lane.cpu_seconds() if self.lane else None
        return self

    def elapsed(self):
        return time.monotonic() - self._t0

    def stop(self):
        wall = time.monotonic() - self._t0
        self.wall = wall
        out = {}
        if self._ps is not None and self._sys0 is not None:
            sys1 = self._ps.cpu_times()
            busy = sum(sys1) - sys1.idle - (sum(self._sys0) - self._sys0.idle)
            total = sum(sys1) - sum(self._sys0)
            if total > 0:
                out["cpu_busy_percent_window"] = round(100.0 * busy / total, 1)
        if self._lane0 is not None and self.lane is not None:
            lane1 = self.lane.cpu_seconds()
            if lane1 is not None and wall > 0:
                used = max(0.0, lane1 - self._lane0)
                out["lane_cpu_s"] = round(used, 2)
                out["lane_cores"] = round(used / wall, 2)
        self.result = out
        return out


# Past one other core a CPU-lane number is about half its quiet rate.
BUSY_HOST_CORES = 1.0

# The spread of two quiet runs; further apart, the load differed, not just the moment.
LOAD_DIFF_CORES = 0.3


_LANE_UNREADABLE = "the lane's CPU time could not be read (it exited or restarted)"

# `via` in a snapshot: which host its busy_cores describe.
VIA_LOCAL = "local"
VIA_WSL_INTEROP = "wsl-interop"


def _busy_cores(percent, cpus):
    return round(percent / 100.0 * cpus, 2) if percent is not None and cpus else None


def _snapshot_note(lane, busy, lane_cores, ps):
    """Why `other_cores` is what it is, or why it is missing."""
    if ps is None:
        return "psutil not installed"
    if busy is None:
        return "no CPU time elapsed in the window"
    if lane is None:
        return "no lane named: every busy core counts as other load"
    if not lane.available:
        # From WSL2 the counters are the VM's; a remote lane shares no cores with this host.
        return f"the lane's host is not visible from here: {lane.reason}"
    if lane_cores is None:
        return _LANE_UNREADABLE
    return None


def _interop_port(lane):
    """The lane's port when only the Windows host can be serving it, else None."""
    if (
        lane is None
        or lane.available
        or getattr(lane, "listens_here", None) is not False
    ):
        return None
    port = _port(getattr(lane, "base_url", None))
    return port if port is not None and winhost.in_wsl() else None


def _windows_host(seconds, lane):
    """winhost.measure() for a lane only Windows sees; {"error"} if it failed; else None."""
    port = _interop_port(lane)
    if port is None:
        return None
    try:
        return winhost.measure(port, seconds)
    except winhost.InteropError as e:
        return {"error": str(e)}
    except Exception as e:  # a load reading must never cost a run
        return {"error": f"{type(e).__name__}: {e}"[:160]}


def _interop_snapshot(reading):
    """A Windows-host reading on the scale of a local one; no listener leaves cores None."""
    load, cpus = reading["load"], reading["cpus"]
    busy = _busy_cores(load.get("cpu_busy_percent_window"), cpus)
    lane_cores = load.get("lane_cores")
    note = None
    if reading["pid"] is None:
        note = (
            f"no process listens on port {reading['port']} on the Windows host either"
        )
    elif lane_cores is None:
        note = _LANE_UNREADABLE
    return {
        "busy_cores": busy,
        "lane_cores": lane_cores,
        "other_cores": None if note else round(max(0.0, busy - lane_cores), 2),
        "seconds": round(reading["wall"], 2),
        "cpus": cpus,
        "note": note,
        "via": VIA_WSL_INTEROP,
    }


def load_snapshot(seconds=3, lane=None):
    """How busy the lane's host was just before a run, net of the lane; never raises.

    `lane` is a LaneProcess, a base URL, or None.
    """
    if isinstance(lane, str):
        lane = LaneProcess(lane)
    ps = _psutil()
    window = Window(lane if lane is not None and lane.available else None).start()
    host = _windows_host(seconds, lane)
    # A Windows reading that failed early still leaves the local fallback its full window.
    time.sleep(max(0.0, seconds - window.elapsed()))
    load = window.stop()
    if host is not None and "error" not in host:
        return _interop_snapshot(host)
    cpus = ps.cpu_count() if ps else None
    busy = _busy_cores(load.get("cpu_busy_percent_window"), cpus)
    lane_cores = load.get("lane_cores")
    note = _snapshot_note(lane, busy, lane_cores, ps)
    if host is not None:
        note = f"{note}; the Windows host through WSL interop: {host['error']}"
    other = None
    if note is None:
        other = round(max(0.0, busy - lane_cores), 2)
    elif lane is None and busy is not None:
        other = busy
    return {
        "busy_cores": busy,
        "lane_cores": lane_cores,
        "other_cores": other,
        "seconds": round(window.wall, 2),
        "cpus": cpus,
        "note": note,
        "via": VIA_LOCAL,
    }


def load_line(snapshot):
    """The console line announcing a run-start snapshot, with a warning when busy."""
    snapshot = snapshot or {}
    other = snapshot.get("other_cores")
    where = " on the Windows host" if snapshot.get("via") == VIA_WSL_INTEROP else ""
    if other is None:
        busy = snapshot.get("busy_cores")
        seen = f"{busy:.2f} busy cores{where or ' here'}; " if busy is not None else ""
        return f"  Host load:    {seen}other load unknown -- {snapshot.get('note')}"
    line = (
        f"  Host load:    {other:.2f} other cores{where} over the "
        f"{snapshot.get('seconds', 0):.0f} s before the first request"
    )
    if snapshot.get("lane_cores") is not None:
        line += f" (the lane itself: {snapshot['lane_cores']:.2f})"
    if other > BUSY_HOST_CORES:
        line += (
            "\n  WARNING: over one core busy with something else. A CPU lane "
            "measured now\n  reads about half its quiet rate; close what you "
            "can (the IDE, a scan) first."
        )
    return line
