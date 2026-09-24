"""The Windows host's CPU load, read from a WSL2 harness through interop.

`bench_coding` and `bench_agent` run in WSL2 against lanes that run on the
Windows host, reached on loopback through mirrored networking. psutil there
lists Linux processes and the VM's CPU counters, which read quiet while
VS Code or a Defender scan loads the host, so every run-start `host_load` from
WSL2 recorded `other_cores: null` (roadmap P1.5). provenance already reaches
the Windows side through /mnt/c for the GenieX binary; this reaches it through
powershell.exe for the load.

One powershell.exe call takes both samples around a Start-Sleep, so its start
(module loading, the listener lookup) lands before the window rather than in
it. It reads the WMI/CIM classes, never typeperf: counter paths are localised,
and on this host's German Windows "Processor(_Total)" / "% Processor Time" is
"Prozessor(_Total)" / "Prozessorzeit (%)". The numbers come back raw and the
arithmetic is done here, where it is tested.

`measure()` raises InteropError with the reason for every failure;
`hostload.load_snapshot()` turns that into its note and keeps `other_cores`
null, as it was before this module existed.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import platform
import shutil
import subprocess  # nosec B404 -- runs powershell.exe with a script built here
import sys


POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# WSL registers its interop handler here; newer builds name it WSLInterop-late.
_INTEROP_GLOB = "/proc/sys/fs/binfmt_misc/WSLInterop*"

# powershell.exe's own start before the window opens, plus the listener
# lookup (Get-NetTCPConnection loads the NetTCPIP module: 0.6 s). A 3 s
# reading took 4.5 s end to end from WSL2 here (2026-09-24, host loaded);
# the margin is for a cold start. Past it the reading is abandoned, named.
OVERHEAD_S = 20

TICKS_PER_S = 10_000_000  # WMI times and Stopwatch ticks are 100 ns

# Windows PowerShell 5.1: every Windows 10/11 has it, pwsh 7 is optional.
# Processor raw counters: PercentProcessorTime is PERF_100NSEC_TIMER_INV, i.e.
# the core's idle time, so busy cores = n - d(idle) / d(Timestamp_Sys100NS).
# The lane is the tree under the process listening on the port, as psutil
# walks it natively; a child counts only if it started after its parent,
# because Windows keeps a parent id after the parent is gone.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Get-Idle {
    $idle = [uint64]0; $ts = [uint64]0; $n = 0
    foreach ($c in Get-CimInstance -ClassName Win32_PerfRawData_PerfOS_Processor) {
        if ($c.Name -eq '_Total') { continue }
        $idle += [uint64]$c.PercentProcessorTime
        $ts = [uint64]$c.Timestamp_Sys100NS
        $n += 1
    }
    @{ idle = $idle; ts = $ts; n = $n }
}
function Get-LaneTicks($rootId, $started) {
    $kids = @{}; $root = $null
    $all = Get-CimInstance -ClassName Win32_Process -Property ProcessId,ParentProcessId,CreationDate,KernelModeTime,UserModeTime
    foreach ($p in $all) {
        if ($p.ProcessId -eq $rootId) { $root = $p }
        $key = [int]$p.ParentProcessId
        if (-not $kids.ContainsKey($key)) { $kids[$key] = New-Object System.Collections.ArrayList }
        [void]$kids[$key].Add($p)
    }
    if ($null -eq $root) { return $null }
    if ($null -ne $started -and $root.CreationDate -ne $started) { return $null }
    $ticks = [uint64]0; $seen = @{}; $todo = New-Object System.Collections.Stack
    $todo.Push($root)
    while ($todo.Count -gt 0) {
        $p = $todo.Pop()
        if ($seen.ContainsKey([int]$p.ProcessId)) { continue }
        $seen[[int]$p.ProcessId] = $true
        $ticks += [uint64]$p.KernelModeTime + [uint64]$p.UserModeTime
        foreach ($c in $kids[[int]$p.ProcessId]) {
            if ($c.CreationDate -ge $p.CreationDate) { $todo.Push($c) }
        }
    }
    @{ ticks = $ticks; started = $root.CreationDate }
}
$port = __PORT__
$rootId = $null
$listen = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
# PIDs 0 and 4 are Idle and System (http.sys): their tree is the machine.
if ($listen.Count -gt 0 -and $listen[0].OwningProcess -gt 4) { $rootId = [int]$listen[0].OwningProcess }
$clock = [Diagnostics.Stopwatch]::StartNew()
$lane0 = $null; $at0 = $null; $lane1 = $null; $at1 = $null
if ($null -ne $rootId) {
    $t = $clock.Elapsed.Ticks
    $lane0 = Get-LaneTicks $rootId $null
    $at0 = [long](($t + $clock.Elapsed.Ticks) / 2)
}
$cpu0 = Get-Idle
Start-Sleep -Milliseconds __MS__
$cpu1 = Get-Idle
if ($null -ne $lane0) {
    $t = $clock.Elapsed.Ticks
    $lane1 = Get-LaneTicks $rootId $lane0.started
    $at1 = [long](($t + $clock.Elapsed.Ticks) / 2)
}
$l0 = $null; if ($null -ne $lane0) { $l0 = $lane0.ticks }
$l1 = $null; if ($null -ne $lane1) { $l1 = $lane1.ticks }
[ordered]@{
    port = $port; pid = $rootId; cpus = @($cpu0.n, $cpu1.n)
    idle = @($cpu0.idle, $cpu1.idle); ts = @($cpu0.ts, $cpu1.ts)
    lane = @($l0, $l1); lane_at = @($at0, $at1)
} | ConvertTo-Json -Compress
"""


class InteropError(RuntimeError):
    """Why the Windows host could not be read; the text becomes the note."""


def _kernel_release():
    try:
        with open("/proc/sys/kernel/osrelease", encoding="ascii") as f:
            return f.read()
    except OSError:
        return platform.release()


def in_wsl():
    """True inside WSL (1 or 2): the interop handler, or Microsoft's kernel."""
    if not sys.platform.startswith("linux"):
        return False
    return bool(glob.glob(_INTEROP_GLOB)) or "microsoft" in _kernel_release().lower()


def find_powershell():
    """Windows PowerShell as WSL sees it, or None (interop off, C: elsewhere)."""
    if os.path.isfile(POWERSHELL):
        return POWERSHELL
    return shutil.which("powershell.exe")


def script(port, seconds):
    """The PowerShell that samples the host around a `seconds` sleep."""
    ms = max(0, round(float(seconds) * 1000))
    return _SCRIPT.replace("__PORT__", str(int(port))).replace("__MS__", str(ms))


def _run(argv, timeout):
    """(returncode, stdout, stderr) of one powershell.exe call; the test seam."""
    p = subprocess.run(  # nosec B603 -- powershell.exe, a script built above
        argv,
        capture_output=True,
        text=True,
        # The console codepage of a German Windows is not UTF-8; the JSON line
        # is ASCII, and an error text must not raise before it is reported.
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return p.returncode, p.stdout, p.stderr


def _first_line(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[0][:160] if lines else "no output"


def measure(port, seconds):
    """The Windows host's load over `seconds`, net of the process on `port`.

    Returns {"load", "wall", "cpus", "pid", "port"}: `load` has the keys of
    hostload.Window.stop() (cpu_busy_percent_window, lane_cpu_s, lane_cores),
    `pid` is None when nothing on the host listens on the port. Raises
    InteropError for every way the reading can fail.
    """
    exe = find_powershell()
    if exe is None:
        raise InteropError(
            "powershell.exe not found (interop off, or C: not at /mnt/c)"
        )
    encoded = base64.b64encode(script(port, seconds).encode("utf-16-le")).decode()
    argv = [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    timeout = seconds + OVERHEAD_S
    try:
        code, out, err = _run(argv, timeout)
    except subprocess.TimeoutExpired as e:
        raise InteropError(
            f"powershell.exe gave no reading within {timeout:.0f} s"
        ) from e
    except OSError as e:  # interop disabled: exec format error
        raise InteropError(
            f"powershell.exe did not start: {type(e).__name__}: {e}"
        ) from e
    if code != 0:
        raise InteropError(f"powershell.exe exited {code}: {_first_line(err or out)}")
    return parse(out, port)


def _fields(stdout):
    """The raw samples from the script's JSON line, as integers (None kept)."""
    line = next(
        (ln for ln in reversed((stdout or "").splitlines()) if ln.startswith("{")),
        None,
    )
    if line is None:
        raise InteropError(
            f"no reading in powershell.exe output: {_first_line(stdout)}"
        )
    try:
        doc = json.loads(line)
        cpus = [int(v) for v in doc["cpus"]]
        idle = [int(v) for v in doc["idle"]]
        ts = [int(v) for v in doc["ts"]]
        lane = [None if v is None else int(v) for v in doc["lane"]]
        at = [None if v is None else int(v) for v in doc["lane_at"]]
        pid = None if doc["pid"] is None else int(doc["pid"])
    except (ValueError, KeyError, TypeError) as e:
        raise InteropError(f"unreadable reading: {type(e).__name__}: {e}"[:160]) from e
    if not (len(cpus) == len(idle) == len(ts) == len(lane) == len(at) == 2):
        raise InteropError("unreadable reading: not two samples")
    return cpus, idle, ts, lane, at, pid


def parse(stdout, port):
    """measure()'s result from the script's output; InteropError when unusable.

    The lane's two samples bracket the counters' (a Win32_Process query on
    each side, 0.1-0.2 s here), so its cores are taken over its own span,
    stamped at each query's midpoint, not over the counters' shorter one.
    """
    cpus, idle, ts, lane, at, pid = _fields(stdout)
    span = ts[1] - ts[0]
    if cpus[0] != cpus[1] or cpus[1] <= 0 or span <= 0 or idle[1] < idle[0]:
        raise InteropError(f"inconsistent counters: cpus {cpus}, ts {ts}, idle {idle}")
    idle_share = min(1.0, (idle[1] - idle[0]) / (span * cpus[1]))
    load = {"cpu_busy_percent_window": round(100.0 * (1.0 - idle_share), 1)}
    known = None not in lane and None not in at
    if pid is not None and known and at[1] > at[0]:
        used = max(0.0, (lane[1] - lane[0]) / TICKS_PER_S)
        load["lane_cpu_s"] = round(used, 2)
        load["lane_cores"] = round(used / ((at[1] - at[0]) / TICKS_PER_S), 2)
    return {
        "load": load,
        "wall": span / TICKS_PER_S,
        "cpus": cpus[1],
        "pid": pid,
        "port": port,
    }
